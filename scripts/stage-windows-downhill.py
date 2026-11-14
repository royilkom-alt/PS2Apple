#!/usr/bin/env python3
"""Stage the source, disc extractor and reusable iPhoneOS libraries for Downhill."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "Backend"))
from builder import MIN_IOS, TOOLS, output, profile_for, run
from ports.downhill import build as port

WASI_SHA256 = "cccb5c323a9b34f0349a9b09e8804a0a7632c68c3310f4b5f437ed57d7e71d8f"
COFF_CLANG_SHA256 = "6692256b824551ab2cae62d4b8351b2357ec79fe5ddd6129b5f6d95616140383"
WASI_BIN = {"clang.exe", "clang.cfg", "wasm-ld.exe", "llvm-ar.exe", "llvm-nm.exe", "llvm-objcopy.exe",
            "llvm-cxxfilt.exe", "libxml2.dll"}
WASI_CRT = ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll")
PYTHON_PACKAGES = {"colorama", "crunch64", "intervaltree", "n64img", "pypng", "pyyaml", "rabbitizer",
                   "sortedcontainers", "spimdisasm", "splat64", "tqdm"}
WASI_LIB = "share/wasi-sysroot/lib/wasm32-wasip1/"
WASI_BUILTINS = "lib/clang/23/lib/wasm32-unknown-wasi/libclang_rt.builtins.a"


def stage_tools(archive: Path, digest: str, target: Path, wanted) -> None:
    with archive.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
            raise ValueError(f"Unexpected checksum for {archive.name}.")
    with tarfile.open(archive, "r|gz") as source:
        for member in source:
            parts = PurePosixPath(member.name).parts[1:]
            name = "/".join(parts)
            if member.isfile() and ".." not in parts and wanted(name):
                path = target.joinpath(*parts)
                path.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as stream, path.open("wb") as destination:
                    shutil.copyfileobj(stream, destination)


def stage_python(wheels: Path, target: Path) -> None:
    inputs = {path.name.split("-", 1)[0].lower(): path for path in sorted(wheels.glob("*.whl"))}
    if missing := PYTHON_PACKAGES - inputs.keys():
        raise ValueError(f"Downhill is missing Python wheels: {', '.join(sorted(missing))}.")
    for name in sorted(PYTHON_PACKAGES):
        path = inputs[name]
        with zipfile.ZipFile(path) as archive:
            archive.extractall(target)
    configuration = target / "splat/util/conf.py"
    text = configuration.read_text(encoding="utf-8")
    # Windows uses the standard PyYAML reader without the optional Unix accelerator.
    accelerator = "# This unused import makes the yaml library faster. don't remove\nimport pylibyaml  # noqa: F401\n"
    if accelerator not in text:
        raise ValueError("The staged splat configuration does not match splat 0.50.0.")
    configuration.write_text(text.replace(accelerator, "", 1), encoding="utf-8")
    # The N64 display-list module requires a separate native extension. This pack only splits PS2.
    formats = target / "splat/segtypes/n64/__init__.py"
    text = formats.read_text(encoding="utf-8")
    formats.write_text(text.replace("from . import gfx as gfx\n", "", 1), encoding="utf-8")


def stage_source(cache: Path, profile: dict, target: Path) -> None:
    # Copy the pinned source snapshot and apply the exact patches used by the Mac builder.
    with tempfile.TemporaryDirectory(prefix="downhill-source-", dir=target.parent) as temporary:
        archive = Path(temporary) / "source.tar"
        run(["git", "-C", cache, "archive", "-o", archive, profile["revision"]])
        target.mkdir()
        with tarfile.open(archive) as bundle:
            bundle.extractall(target, filter="data")
    for patch in port.PORT_PATCHES:
        run(["git", "apply", patch], cwd=target)
    port.stage_touch_overlay(target)
    # Upstream core caching relies on Git. The shipped copy builds in a new folder for each job.
    core = target / "port/tools/build_wasm.py"
    text = core.read_text(encoding="utf-8")
    original = "    key = core_inputs_key()"
    if text.count(original) != 1:
        raise ValueError("The pinned Downhill core build entry point changed.")
    core.write_text(text.replace(original, "    key = '' if os.environ.get('DHD_MODULE_CACHE') == '0' else core_inputs_key()"),
                    encoding="utf-8")


def stage_ios(cache: Path, profile: dict, sdl: Path, renderer: Path, target: Path) -> None:
    # The reference contributes import names and independent runtime objects. Game code,
    # generated VU objects and the complete executable are built later from the user's disc.
    reference = cache / "build/host64-ios"
    excluded = "port_runtime_vu0_interp.c.o"
    objects = sorted((reference / "obj").glob("port_*.o"))
    archive = target / "libdownhill_host.a"
    target.mkdir()
    commands = json.loads((reference / "commands.json").read_text(encoding="utf-8"))
    if not any(str(renderer.resolve()) in command and str((sdl / "lib/libSDL3.a").resolve()) in command
               for command in commands):
        raise ValueError("The staged renderer and SDL must come from the verified Downhill iPhoneOS release link.")
    members = output(["/usr/bin/ar", "-t", reference / "libhost64.a"]).splitlines()
    host = [p for p in objects if p.name in members and p.name != excluded]
    if {p.name for p in host} != {name for name in members if name.endswith(".o") and name != excluded}:
        raise ValueError("The Downhill host objects do not match the reference build.")
    if excluded not in members or not host:
        raise ValueError("The Downhill reference is missing its host compilation outputs.")
    windows_kit = importlib.util.spec_from_file_location("stagekit", ROOT / "scripts/stage-windows-kit.py")
    stagekit = importlib.util.module_from_spec(windows_kit)
    windows_kit.loader.exec_module(stagekit)
    run(["/usr/bin/ar", "rcs", archive, *host])
    shutil.copyfile(renderer, target / "libdhd_render.a")
    shutil.copyfile(sdl / "lib/libSDL3.a", target / "libSDL3.a")
    shutil.copytree(sdl / "include", target / "SDL")
    sdk = output(["/usr/bin/xcrun", "--sdk", "iphoneos", "--show-sdk-version"])
    stagekit.make_imports(reference / "DownhillRevived", sdk, target, cxx=False)
    link = target / "link.json"
    data = json.loads(link.read_text(encoding="utf-8"))
    data["revision"] = profile["revision"]
    link.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mac-source", required=True, type=Path)
    parser.add_argument("--renderer", required=True, type=Path)
    parser.add_argument("--sdl", required=True, type=Path)
    parser.add_argument("--simde", required=True, type=Path)
    parser.add_argument("--coff-clang", required=True, type=Path)
    parser.add_argument("--wasi", required=True, type=Path)
    parser.add_argument("--msvc-crt", required=True, type=Path, help="Directory containing the x64 app-local runtime DLLs")
    parser.add_argument("--wabt", required=True, type=Path, help="Installed Windows WABT 1.0.42 with internal SHA-256")
    parser.add_argument("--wheels", required=True, type=Path)
    parser.add_argument("kit", type=Path)
    args = parser.parse_args()
    profile = profile_for(ROOT, "downhill").data
    with tempfile.TemporaryDirectory(prefix="downhill-kit-", dir=args.kit) as temporary:
        target = Path(temporary) / "Downhill"
        target.mkdir()
        source = target / "Source"
        stage_source(args.mac_source, profile, source)
        stage_tools(args.wasi, WASI_SHA256, target / "WASI",
                    lambda name: name in {*(f"bin/{n}" for n in WASI_BIN), WASI_BUILTINS, "LICENSE"}
                    or name.startswith(("share/wasi-sysroot/include/", "lib/clang/23/include/"))
                    or name in {WASI_LIB + "libc.a", WASI_LIB + "libwasi-emulated-process-clocks.a"})
        for name in WASI_CRT:
            shutil.copyfile(args.msvc_crt / name, target / "WASI/bin" / name)
        wabt = target / "WABT"
        for path in ("bin/wasm2c.exe", "bin/wasm-objdump.exe", "include/wasm-rt.h", "LICENSE", "PicoSHA2-LICENSE"):
            destination = wabt / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(args.wabt / path, destination)
        shutil.copytree(args.wabt / "share/wabt/wasm2c", wabt / "share/wabt/wasm2c")
        coff = target / "COFF"
        coff.mkdir()
        with args.coff_clang.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != COFF_CLANG_SHA256:
                raise ValueError("The COFF assembler must be the pinned Windows LLVM 22.1.7 clang.exe.")
        shutil.copyfile(args.coff_clang, coff / "clang.exe")
        shutil.copytree(args.simde / "simde", target / "SIMDe/simde")
        shutil.copyfile(args.simde / "COPYING", target / "SIMDe/COPYING")
        stage_python(args.wheels, target / "Python")
        stage_ios(args.mac_source, profile, args.sdl, args.renderer, target / "iPhoneOS")
        target.rename(args.kit / "Downhill")


if __name__ == "__main__":
    main()
