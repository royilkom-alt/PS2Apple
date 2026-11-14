"""Boot synthetic ISO images through the production desktop AOT pipeline."""
from pathlib import Path
import argparse
import importlib.util
import struct
import sys
import tempfile
from unittest.mock import patch


def write_disc(path: Path, words: dict[int, int]) -> Path:
    """Put a small, sectionless MIPS executable in an ISO9660 fixture."""
    from test_backend import ISOImage, SECTOR, make_iso, record

    name = b"BOOT.ELF"
    make_iso(path, name)
    base = 0x00100000
    size = max(words) + 4
    elf = bytearray(0x100 + size)
    elf[:7] = b"\x7fELF\x01\x01\x01"
    struct.pack_into("<HHIIIIIHHHHHH", elf, 16, 2, 8, 1, base, 52, 0, 0, 52, 32, 1, 40, 0, 0)
    struct.pack_into("<8I", elf, 52, 1, 0x100, base, 0, size, size, 5, 4)
    for offset, word in words.items():
        struct.pack_into("<I", elf, 0x100 + offset, word)
    assert len(elf) <= SECTOR
    with path.open("r+b") as disc:
        disc.seek(20 * SECTOR)
        first_size = disc.read(1)[0]
        disc.seek(20 * SECTOR + first_size)
        disc.write(record(name + b";1", 22, len(elf)))
        disc.seek(22 * SECTOR)
        disc.write(elf)
    boot = path.with_suffix(".elf")
    with ISOImage(path) as disc:
        _, entry = disc.boot()
        disc.extract_boot(entry, boot)
    return boot


def word_offsets(instructions: list[int]) -> dict[int, int]:
    return {index * 4: value for index, value in enumerate(instructions)}


def pointer_table() -> dict[int, int]:
    # Indirectly call eighteen constructors beyond the initially reachable text.
    words = word_offsets([
        0x3C100010, 0x36100400, 0x24110012,       # s0 = table, s1 = 18
        0x8E080000, 0x0100F809, 0x26100004,       # lw t0, 0(s0); jalr t0; advance s0
        0x2631FFFF, 0x1620FFFB, 0,                # bne s1, zero, loop
        0x24090011, 0x14490003, 0,                # confirm the last constructor ran
        0x0804000C, 0,                           # idle
        0x24030004, 0x0000000C, 1,                # Exit, then stop static scanning
    ])
    for index in range(18):
        address = 0x200 + index * 0x10
        words[0x400 + index * 4] = 0x00100000 + address
        words[address] = 0x03E00008              # jr ra
        words[address + 4] = 0x24020000 | index   # addiu v0, zero, index
    return words


def updated_overlay() -> dict[int, int]:
    # Call a RAM routine, then call its delay slot as a separate entry. Rewrite
    # another routine's delay slot after the initial capture and call it too.
    code = [0x3C080022]                          # t0 = 0x00220000

    def write_ram(offset: int, word: int) -> None:
        code.extend([0x3C090000 | (word >> 16), 0x35290000 | (word & 0xFFFF), 0xAD090000 | offset])

    for offset, instruction in [
        (0x00, 0x08088008),                      # j 0x00220020
        (0x04, 0x24020001),                      # addiu v0, zero, 1
        (0x08, 0x03E00008), (0x0C, 0x24420001),  # jr ra; addiu v0, v0, 1
        (0x20, 0x03E00008), (0x24, 0x24420001),
        (0x40, 0x03E00008), (0x44, 0x24020063),  # original v0 = 99
    ]:
        write_ram(offset, instruction)
    code.extend([0x0100F809, 0])                 # jalr t0
    code.extend([0x250A0004, 0x0140F809, 0])     # jalr t0 + 4
    code.extend([0x24090002, 0x14490000, 0])     # expect the directly called entry to return 2
    checks = [len(code) - 2]
    write_ram(0x44, 0x2402002A)                  # current v0 = 42
    code.extend([0x250A0040, 0x0140F809, 0])     # jalr t0 + 0x40
    code.extend([0x2409002A, 0x14490000, 0])     # expect the new image to return 42
    checks.append(len(code) - 2)
    code.extend([0x08040000 | len(code), 0])    # idle
    for branch in checks:
        code[branch] |= len(code) - branch - 1
    code.extend([0x24030004, 0x0000000C])        # Exit
    return word_offsets(code)


def rewritten_code(versions: int) -> dict[int, int]:
    # Write consecutive versions of one dynamically linked function.
    # Each version changes the return value in the delay slot at the same address.
    return word_offsets([
        0x3C080022,                            # t0 = 0x00220000
        0x3C0903E0, 0x35290008, 0xAD090000,     # write jr ra
        0x24100001, 0x24110000 | (versions + 1),  # s0 = 1, end = versions + 1
        0x3C092402, 0x01304825, 0xAD090004,     # write addiu v0, zero, s0
        0x0100F809, 0,                         # jalr t0
        0x14500006, 0,                         # wrong value: Exit
        0x26100001, 0x1611FFF7, 0,             # next version
        0x08040010, 0,                         # idle at 0x00100040
        0x24030004, 0x0000000C,
    ])


def independent_overlay_entries() -> dict[int, int]:
    # Both calls are recorded from one image. Rewriting the second function
    # must leave the first callable without recapturing it.
    return word_offsets([
        0x3C080022,                            # t0 = 0x00220000
        0x3C0903E0, 0x35290008,                # t1 = jr ra
        0xAD090000, 0xAD090010,                # return at both entries
        0x3C092402, 0x35290001, 0xAD090004,     # first returns 1
        0x35290003, 0xAD090014,                # second returns 3
        0x0100F809, 0,                         # jalr t0
        0x250A0010, 0x0140F809, 0,             # jalr t0 + 0x10
        0x3C092402, 0x35290002, 0xAD090014,     # rewrite second to return 2
        0x0100F809, 0,                         # jalr t0
        0x24090001, 0x14490003, 0,             # incorrect return: Exit
        0x08040017, 0,                         # idle
        0x24030004, 0x0000000C,
    ])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", choices=("pointer-table", "overlay", "invalid-slot", "rewritten-code",
                                         "independent-entries", "recorder-limit", "automatic-recorder"))
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--cmake", default="cmake")
    parser.add_argument("--ninja", default="ninja")
    parser.add_argument("--compiler", default="clang++")
    parser.add_argument("--tools", type=Path, required=True)
    args = parser.parse_args()
    project = args.project.resolve()
    sys.path.insert(0, str(project / "tests"))
    backend = project / "Backend"
    spec = importlib.util.spec_from_file_location("aot_smoke_recipe", backend / "ports/generic/build.py")
    recipe = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = recipe
    spec.loader.exec_module(recipe)
    recipe.TOOLS.update(cmake=args.cmake, ninja=args.ninja)
    versions = 513 if args.case == "recorder-limit" else 18
    def rewritten_fixture():
        return rewritten_code(versions)

    fixtures = {"pointer-table": pointer_table, "overlay": updated_overlay,
                "rewritten-code": rewritten_fixture, "recorder-limit": rewritten_fixture,
                "independent-entries": independent_overlay_entries, "automatic-recorder": rewritten_fixture,
                "invalid-slot": lambda: {0: 0x08040000, 4: 1}}
    with tempfile.TemporaryDirectory(prefix="PS2 AOT discovery ") as temporary:
        stage = Path(temporary)
        iso = stage / "fixture.iso"
        boot = write_disc(iso, fixtures[args.case]())
        deps = recipe.Dependencies(backend / "engine", args.tools.resolve(), "", stage, stage, args.compiler)
        if args.case == "invalid-slot":
            try:
                recipe.prepare_game_code(deps, iso, boot, stage)
            except recipe.BuildError as error:
                assert "0x00100004" in str(error) and "word 0x00000001" in str(error), error
                return
            raise AssertionError("The invalid instruction should have stopped the build.")
        if args.case == "independent-entries":
            with patch.object(recipe, "DISCOVERY_ROUNDS", 1):
                _, report = recipe.prepare_game_code(deps, iso, boot, stage, learn_ee=True)
        elif args.case == "automatic-recorder":
            # With only two passes, recovering one entry per strict miss cannot
            # run the 18 versions. The desktop batch recorder must collect them.
            with patch.object(recipe, "DISCOVERY_ROUNDS", 2):
                _, report = recipe.prepare_game_code(deps, iso, boot, stage, general_probe=True,
                                                     disc_id=("SLUS-29999", recipe.digest(boot)))
        elif args.case in ("rewritten-code", "recorder-limit"):
            _, report = recipe.prepare_game_code(deps, iso, boot, stage, learn_ee=True)
        else:
            _, report = recipe.prepare_game_code(deps, iso, boot, stage)
        if args.case == "automatic-recorder":
            captures = stage / "Desktop Code Recorder" / "Captures"
            assert len(list(captures.glob("*.bin"))) == versions, report
            assert len((stage / "entries.txt").read_text().splitlines()) == versions, report
            assert "4200 guest fields" in report and "scripted menu inputs" in report, report
            print(f"Automatically captured {versions} revisions and verified 4,200 fields in a strict AOT replay.")
            return
        assert "1200 guest fields" in report, report
        entries = (stage / "entries.txt").read_text().splitlines()
        if args.case == "pointer-table":
            assert entries == ["100200"], entries
        elif args.case == "independent-entries":
            assert len(entries) == 2 and len(list((stage / "Overlays").glob("*.bin"))) == 1, entries
            print("The unchanged entry remained callable on the first strict AOT pass.")
            return
        elif args.case in ("rewritten-code", "recorder-limit"):
            assert len(entries) == versions and len(list((stage / "Overlays").glob("*.bin"))) == versions, entries
            print(f"All {versions} versions captured in one desktop run and verified by the strict AOT runner.")
            return
        else:
            assert len(entries) == 3 and len(list((stage / "Overlays").glob("*.bin"))) == 2, entries
        print(f"Passed {args.case} through {len(entries) + 1} boot passes.")


if __name__ == "__main__":
    main()
