#!/usr/bin/env python3
"""Generate the pinned MKSM port's C++ sources from a local, verified retail ELF."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import signal
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from builder import BuildError, MIN_IOS, TOOLS, cancelled, checkout, output, run  # noqa: E402

GAME = Path("games/mk-shaolin-monks")


def prepare_compiler(source: Path, build: Path) -> Path:
    compiler = build / "ps2xRecomp/ps2_recomp"
    if compiler.is_file():
        print("Using the cached Shaolin Monks recompiler.", flush=True)
        return compiler
    run([TOOLS["cmake"], "-S", source, "-B", build, "-G", "Ninja",
         f"-DCMAKE_MAKE_PROGRAM={TOOLS['ninja']}", "-DCMAKE_BUILD_TYPE=Release",
         "-DPS2X_BUILD_RECOMP=ON", "-DPS2X_BUILD_ANALYZER=OFF", "-DPS2X_BUILD_RUNTIME=OFF",
         "-DPS2X_BUILD_TEST=OFF", "-DPS2X_BUILD_STUDIO=OFF"])
    run([TOOLS["cmake"], "--build", build, "--target", "ps2_recomp", "--parallel", "2"])
    if not compiler.is_file():
        raise BuildError("The static recompiler was not produced by CMake.")
    return compiler


def prepare_ios_iop(source: Path, build: Path) -> Path:
    library = build / "libps2_iop.a"
    if not library.is_file():
        sdk = output(["/usr/bin/xcrun", "--sdk", "iphoneos", "--show-sdk-path"])
        print("Building the Shaolin Monks IOP services for iPhoneOS ARM64.", flush=True)
        run([TOOLS["cmake"], "-S", source / "ps2xIOP", "-B", build, "-G", "Ninja",
             f"-DCMAKE_MAKE_PROGRAM={TOOLS['ninja']}", "-DCMAKE_BUILD_TYPE=Release",
             "-DCMAKE_SYSTEM_NAME=iOS", f"-DCMAKE_OSX_SYSROOT={sdk}", "-DCMAKE_OSX_ARCHITECTURES=arm64",
             f"-DCMAKE_OSX_DEPLOYMENT_TARGET={MIN_IOS}", "-DCMAKE_TRY_COMPILE_TARGET_TYPE=STATIC_LIBRARY",
             "-DPS2X_IOP_ENABLE_PLUGINS=OFF"])
        run([TOOLS["cmake"], "--build", build, "--target", "ps2_iop", "--parallel", "2"])
    run(["/usr/bin/xcrun", "--sdk", "iphoneos", "lipo", "-verify_arch", "arm64", library])
    return library


def generate(source: Path, compiler: Path, iop_library: Path, elf: Path, target: Path, profile: dict):
    with elf.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != profile["bootSHA256"]:
        raise BuildError("The Shaolin Monks boot ELF changed or does not match the supported USA disc.")
    if target.exists():
        raise BuildError(f"The port source folder already exists at {target}.")

    with tempfile.TemporaryDirectory(prefix=".ps2apple-", dir=target.parent) as temporary:
        stage = Path(temporary)
        archive = stage / "source.tar"
        workspace = stage / "Port"
        workspace.mkdir()
        run(["git", "archive", "--format=tar", f"--output={archive}", profile["revision"]], cwd=source)
        with tarfile.open(archive) as package:
            package.extractall(workspace, filter="data")
        run([sys.executable, "-u", workspace / "tools/project.py", "generate",
             "--elf", elf, "--recompiler", compiler], cwd=workspace)

        runtime = workspace / GAME / "runtime"
        expected = [runtime / "output/register_functions.cpp", runtime / "kernel-hooks.cpp",
                    runtime / "pr244-output/register_functions.cpp"]
        if any(not item.is_file() or item.stat().st_size == 0 for item in expected):
            raise BuildError("The recompiler did not generate all Shaolin Monks registration sources.")
        for config in (workspace / GAME / "config").glob("*.local.toml"):
            config.unlink()
        ios = workspace / "ios-arm64"
        ios.mkdir()
        shutil.copy2(iop_library, ios / iop_library.name)
        info = {
            "title": profile["title"], "serial": profile["serial"], "bootSHA256": digest,
            "upstream": profile["upstream"], "revision": profile["revision"], "output": "sources",
            "iopLibrary": f"ios-arm64/{iop_library.name}",
        }
        (workspace / "PS2Apple-Port.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
        workspace.rename(target)


def main() -> int:
    signal.signal(signal.SIGTERM, cancelled)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "generate"))
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--elf", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.action == "generate" and (args.elf is None or args.output is None):
        parser.error("generate requires --elf and --output")
    try:
        profile = json.loads(args.profile.read_text(encoding="utf-8"))
        cache = args.cache.resolve()
        cache.mkdir(parents=True, exist_ok=True)
        source = cache / "Source"
        checkout(source, f"https://github.com/{profile['upstream']}.git", profile["revision"])
        compiler = prepare_compiler(source, cache / "Compiler")
        iop_library = prepare_ios_iop(source, cache / "IOP-iPhoneOS")
        if args.action == "generate":
            generate(source, compiler, iop_library, args.elf.resolve(), args.output.resolve(), profile)
    except (BuildError, OSError, ValueError, KeyError, tarfile.TarError) as error:
        print(f"ERROR: {error}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
