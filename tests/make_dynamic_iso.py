"""Build a synthetic PS2 ISO whose boot ELF writes and calls an EE routine in RAM."""
from pathlib import Path
import struct
import sys

from test_backend import SECTOR, make_iso, record


def make_dynamic_iso(path: Path, visible_logo: bool = False, *, name: bytes = b"BOOT.ELF") -> None:
    make_iso(path, name)
    elf = bytearray(512)
    elf[:7] = b"\x7fELF\x01\x01\x01"
    entry = 0x00100000
    code = [
        0x3C080022,                                    # lui t0, 0x22
        0x3C0903E0, 0x35290008, 0xAD090000,             # write jr ra at 0x00220000
        0x3C092402, 0x3529002A, 0xAD090004,             # write addiu v0, zero, 42 (delay slot)
        0x0100F809, 0,                                 # jalr t0
        0x2409002A, 0x14490000, 0,                      # branch to Exit if the return value is wrong
    ]
    payload_offset = 0x100
    packets = []
    if visible_logo:
        code += [0x3C081200, 0x24090001, 0xFD090000, 0x24090200, 0xFD090070]  # PMODE and DISPFB1
        code += [0x3C081000, 0x35086000, 0x3C0A0010, 0x354A0100]              # GIF FIFO and packet data
        packets = [
            ((1 << 60) | 0x8004, 0xE),             # packed GS register writes
            (1 << 48, 0x50), (0, 0x51),            # BITBLTBUF and TRXPOS
            ((1 << 32) | 1, 0x52), (0, 0x53),       # TRXREG and TRXDIR
            ((2 << 58) | 0x8001, 0), (0xFF302010, 0),  # IMAGE transfer, a colored pixel
        ]
        for index in range(len(packets)):
            code += [0x79490000 | (index * 16), 0x7D090000]  # lq t1, packet(t2); sq t1, 0(t0)
    code += [0x08000000 | ((entry + len(code) * 4) >> 2), 0]
    code[10] |= len(code) - 11
    code += [0x24030004, 0x0000000C]  # Exit
    struct.pack_into("<HHIIIIIHHHHHH", elf, 16, 2, 8, 1, entry, 52, 0, 0, 52, 32, 1, 40, 0, 0)
    size = payload_offset + 16 * len(packets) if packets else 4 * len(code)
    struct.pack_into("<8I", elf, 52, 1, 84, entry, 0, size, size, 5, 4)
    struct.pack_into(f"<{len(code)}I", elf, 84, *code)
    for index, packet in enumerate(packets):
        struct.pack_into("<2Q", elf, 84 + payload_offset + index * 16, *packet)
    with path.open("r+b") as disc:
        disc.seek(20 * SECTOR)
        first_size = disc.read(1)[0]
        disc.seek(20 * SECTOR + first_size)
        disc.write(record(name + b";1", 22, len(elf)))
        disc.seek(22 * SECTOR)
        disc.write(elf)


if __name__ == "__main__":
    make_dynamic_iso(Path(sys.argv[1]))
