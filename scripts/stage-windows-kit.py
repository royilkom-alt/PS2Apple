#!/usr/bin/env python3
"""Prepare the generic compiler/runtime kit shipped with the Windows builder.

Only the release maintainer runs this on a Mac. The reference link uses a
synthetic PS2 program and serves to list the runtime's system-library imports.
No reference app, disc, game code, or Apple SDK files are shipped in the kit.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import plistlib
import re
import shlex
import shutil
import struct
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Backend"))
from builder import MIN_IOS, TOOLS, output, run
from ipa import stage_ps2apple_icon
from windows_build import aot, ENGINE_ROOT, ENGINE_VERSION, KIT_SCHEMA, ZIG_VERSION

ZIG_WINDOWS_SHA256 = "3a0ed1e8799a2f8ce2a6e6290a9ff22e6906f8227865911fb7ddedc3cc14cb0c"
LLD_WINDOWS_SHA256 = "09e9fd8c1bba1ac771f45f3c79f0c08ad3f1fa249fe64aed33764292f9240e5b"
LLD_LICENSE_SHA256 = "8d85c1057d742e597985c7d4e6320b015a9139385cff4cbae06ffc0ebe89afee"


def pinned(path: Path, expected: str) -> None:
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != expected:
        raise ValueError(f"{path.name} does not match the pinned release.")


def make_reference(deps, build_dir: Path, *, translate=aot.translate, compile_ios=aot.compile_ios) -> Path:
    build_dir.mkdir(parents=True, exist_ok=True)
    # Minimal valid MIPS program with a bounded basic block and an idle loop.
    program = (0x24080001, 0x1000FFFF, 0)
    image = bytearray(256 + len(program) * 4)
    image[:7] = b"\x7fELF\x01\x01\x01"
    struct.pack_into("<HHIIIIIHHHHHH", image, 16, 2, 8, 1, 0x100000, 52, 0, 0, 52, 32, 1, 40, 0, 0)
    struct.pack_into("<8I", image, 52, 1, 256, 0x100000, 0, len(program) * 4, len(program) * 4, 5, 4)
    struct.pack_into("<3I", image, 256, *program)
    elf = build_dir / "fixture.elf"
    elf.write_bytes(image)
    entries = build_dir / "entries.txt"
    entries.write_text("", encoding="ascii")
    generated = build_dir / "generated"
    generated.mkdir()
    empty = build_dir / "empty"
    empty.mkdir()
    translate(deps, elf, generated, entries, empty, empty)
    return compile_ios(deps, generated, build_dir / "iPhoneOS", windows_kit=True)


def make_imports(reference: Path, sdk: str, destination: Path, *, cxx: bool = True) -> None:
    directory = destination / "Imports"
    directory.mkdir()
    # dyld_info reports the actual provider and weak imports, including those
    # supplied by Apple re-exports. Record only names used by our own runtime.
    dylibs = {}
    for line in output(["/usr/bin/xcrun", "dyld_info", "-linked_dylibs", reference]).splitlines():
        if match := re.fullmatch(r"\s+(?:(weak-link)\s+)?(/\S+)", line):
            install = match[2]
            key = Path(install).name.split(".")[0]
            dylibs[key] = (install, bool(match[1]))
    symbols = collections.defaultdict(set)
    for line in output(["/usr/bin/xcrun", "dyld_info", "-imports", reference]).splitlines():
        if match := re.match(r"\s+0x[0-9A-Fa-f]+\s+(\S+)\s+(?:\[weak-import\]\s+)?\(from ([^)]+)\)", line):
            if match[2] in dylibs:
                symbols[match[2]].add(match[1])
            elif not match[2].startswith("<"):
                raise ValueError(f"Unresolved import provider in the iPhoneOS runtime: {match[2]}")
    required = {"UIKit", "Metal", "libSystem", "libobjc"}
    if cxx:
        required.add("libc++")
    if not required <= symbols.keys():
        raise ValueError("The iPhoneOS runtime's system imports are incomplete.")
    # C++ operators and these RTTI records are also shown as weak coalesced
    # definitions. Include their standard provider explicitly for LLD.
    if cxx:
        symbols["libc++"].update({"__ZdaPv", "__ZdlPv", "__Znam", "__Znwm",
                                  "__ZTISt12length_error", "__ZTISt12out_of_range"})
    # Clang can emit these standard calls in generated units independently of
    # which instructions are exercised by the release's synthetic program.
    symbols["libSystem"].update({"_memcpy", "_memmove", "_memset", "_bzero", "_memcmp",
                                 "___stack_chk_fail", "___stack_chk_guard"})
    manifest = []
    for name, (install, weak) in dylibs.items():
        filename = name + ".tbd"
        export = ",\n               ".join(f"'{item}'" for item in sorted(symbols[name]))
        (directory / filename).write_text(
            f"--- !tapi-tbd\ntbd-version: 4\ntargets: [ arm64-ios ]\ninstall-name: '{install}'\n"
            f"exports:\n  - targets: [ arm64-ios ]\n    symbols: [ {export} ]\n...\n",
            encoding="ascii")
        manifest.append({"file": filename, "weak": weak})
    (destination / "link.json").write_text(json.dumps({
        "schema": KIT_SCHEMA, "engine": ENGINE_VERSION, "minimumOS": MIN_IOS, "sdkVersion": sdk,
        "libraries": manifest,
    }, indent=2) + "\n", encoding="ascii")


def make_host(zig: Path, build_dir: Path, target: Path, *, source: Path = ENGINE_ROOT,
              aot_only: bool = False) -> None:
    build_dir.mkdir(parents=True, exist_ok=True)
    toolchain = build_dir / "zig-windows.cmake"
    toolchain.write_text(
        'set(CMAKE_SYSTEM_NAME Windows)\nset(CMAKE_SYSTEM_PROCESSOR x86_64)\n'
        f'set(CMAKE_CXX_COMPILER "{zig}" c++ -target x86_64-windows-gnu)\n',
        encoding="utf-8")
    # CMake treats archiver paths as single files. Use local wrappers for Zig's subcommands.
    for tool in ("ar", "ranlib"):
        wrapper = build_dir / tool
        wrapper.write_text(f"#!/bin/sh\nexec {shlex.quote(str(zig))} {tool} \"$@\"\n", encoding="utf-8")
        wrapper.chmod(0o755)
    os.environ.setdefault("ZIG_GLOBAL_CACHE_DIR", str(build_dir / "zig-global"))
    os.environ.setdefault("ZIG_LOCAL_CACHE_DIR", str(build_dir / "zig-local"))
    host = build_dir / "Windows"
    run([TOOLS["cmake"], "-S", source, "-B", host, "-G", "Ninja",
         f"-DCMAKE_MAKE_PROGRAM={TOOLS['ninja']}", f"-DCMAKE_TOOLCHAIN_FILE={toolchain}",
         f"-DCMAKE_AR={build_dir / 'ar'}", f"-DCMAKE_RANLIB={build_dir / 'ranlib'}",
         "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_CXX_FLAGS=-Wno-error=date-time",
         "-DCMAKE_DISABLE_FIND_PACKAGE_SDL2=ON", "-DPS2APPLE_WINDOWS_KIT=ON",
         f"-DPS2_AOT_ONLY={'ON' if aot_only else 'OFF'}", f"-DPS2APPLE_AOT_PROBE={'OFF' if aot_only else 'ON'}"])
    run([TOOLS["cmake"], "--build", host, "--target", "eerecomp", "vurecomp", "ps2apple_windows_probe",
         "--parallel", str(aot.JOBS)])
    target.mkdir()
    for name in ("eerecomp.exe", "vurecomp.exe", "libps2runtime.a", "libps2apple_windows_probe.a"):
        shutil.copyfile(host / name, target / name)


def stage_bt3(zig: Path, cache: Path, work: Path, target: Path) -> None:
    import importlib
    recipe = importlib.import_module("ports.budokai-tenkaichi-3.build")
    target.mkdir()
    deps = recipe.prepare(cache)
    make_host(zig, work / "host", target / "Probe", source=deps.source, aot_only=True)
    reference = make_reference(deps, work / "reference", translate=recipe.translate, compile_ios=recipe.compile_ios)
    ios = target / "iPhoneOS"
    ios.mkdir()
    for path in (reference.parents[1] / "libps2runtime.a",
                 reference.parents[1] / "ps2apple-ios/libps2apple_ios_launcher.a",
                 deps.sdl / "lib/libSDL3.a", deps.wgpu / "lib/libwgpu_native.a"):
        shutil.copyfile(path, ios / path.name)
    make_imports(reference, output(["/usr/bin/xcrun", "--sdk", "iphoneos", "--show-sdk-version"]), ios)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zig-host", type=Path, required=True)
    parser.add_argument("--zig-windows", type=Path, required=True)
    parser.add_argument("--lld-windows", type=Path, required=True, help="LLVM ld64.lld.exe, with LICENSE.TXT beside it")
    parser.add_argument("--cache", type=Path, required=True, help="Cache for the Mac release toolchains")
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    pinned(args.zig_windows, ZIG_WINDOWS_SHA256)
    pinned(args.lld_windows, LLD_WINDOWS_SHA256)
    license_file = args.lld_windows.with_name("LICENSE.TXT")
    pinned(license_file, LLD_LICENSE_SHA256)
    if output([args.zig_host.resolve(), "version"]) != ZIG_VERSION:
        raise ValueError(f"Expected Zig {ZIG_VERSION} for the Windows probe ABI.")

    destination = args.destination.resolve()
    destination.mkdir(parents=True)
    deps = aot.prepare(args.cache.resolve())
    with tempfile.TemporaryDirectory(prefix="ps2apple-kit-", dir=destination.parent) as temporary:
        work = Path(temporary)
        make_host(args.zig_host.resolve(), work, destination / "Probe")
        reference = make_reference(deps, work)
        ios = destination / "iPhoneOS"
        ios.mkdir()
        build_dir = reference.parents[1]
        for path in (build_dir / "libps2runtime.a", build_dir / "ps2apple-ios/libps2apple_ios_launcher.a",
                     deps.sdl / "lib/libSDL3.a", deps.wgpu / "lib/libwgpu_native.a"):
            shutil.copyfile(path, ios / path.name)
        make_imports(reference, output(["/usr/bin/xcrun", "--sdk", "iphoneos", "--show-sdk-version"]), ios)
        resources = ios / "Resources"
        resources.mkdir()
        with (resources / "Info.plist").open("wb") as stream:
            plistlib.dump({}, stream)
        stage_ps2apple_icon(resources, MIN_IOS)
        shutil.copytree(ROOT / "Backend/ports/generic/ios/Licenses", resources / "Licenses")
        shutil.copyfile(ENGINE_ROOT / "LICENSE", resources / "LICENSE-ps2-native-recomp.txt")
        shutil.copyfile(ENGINE_ROOT / "runtime/src/ipu/NOTICE", resources / "Licenses/Play-MPEG.txt")
        stage_bt3(args.zig_host.resolve(), args.cache.resolve() / "BT3", work / "BT3", destination / "BT3")

    llvm = destination / "LLVM"
    llvm.mkdir()
    shutil.copyfile(args.lld_windows, llvm / "ld64.lld.exe")
    shutil.copyfile(license_file, llvm / "LICENSE.TXT")
    zig = destination / "Zig"
    prefix = f"zig-x86_64-windows-{ZIG_VERSION}/"
    with zipfile.ZipFile(args.zig_windows) as archive:
        for entry in archive.infolist():
            if entry.is_dir() or not entry.filename.startswith(prefix):
                continue
            relative = entry.filename.removeprefix(prefix)
            if relative not in ("zig.exe", "LICENSE") and not relative.startswith("lib/"):
                continue
            path = zig / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as source, path.open("wb") as target:
                shutil.copyfileobj(source, target)


if __name__ == "__main__":
    main()
