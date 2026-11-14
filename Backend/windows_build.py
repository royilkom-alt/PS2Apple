#!/usr/bin/env python3
"""Compile the selected PS2 disc and link an iPhoneOS IPA entirely on the PC."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from builder import BuildError, MIN_IOS, catalog_disc, event, profile_for, progress, run
from disc import DiscError, ISOImage
from engine_source import ENGINE_ROOT, ENGINE_VERSION
from ipa import MH_OBJECT, check_macho, export_ipa
from ports.downhill import windows as downhill
from ports.generic import build as aot

BACKEND = Path(__file__).resolve().parent
bt3 = importlib.import_module("ports.budokai-tenkaichi-3.build")

DISC_PROFILES = {
    "SCUS-97177": "downhill",
    "SLUS-21678": "budokai-tenkaichi-3",
    "SLUS-21087": "shaolin-monks-aot",
    "SCUS-97481": "god-of-war-ii",
}
KIT_SCHEMA = 1
HOST_LIBS = ("libps2apple_windows_probe.a", "libps2runtime.a")
IOS_LIBS = ("libps2apple_ios_launcher.a", "libps2runtime.a", "libSDL3.a", "libwgpu_native.a")
WIN_TARGET = "x86_64-windows-gnu"
IOS_TARGET = f"aarch64-ios.{MIN_IOS}"
ZIG_VERSION = "0.15.2"
LLD_VERSION = "22.1.7"


class LocalKit(aot.Dependencies):
    def __init__(self, root: Path, *, bt3_port: bool = False):
        toolchain = root / "Toolchain"
        self.toolchain = toolchain
        self.zig = toolchain / "Zig" / "zig.exe"
        self.lld = toolchain / "LLVM" / "ld64.lld.exe"
        self.darwin_headers = self.zig.parent / "lib" / "libc" / "include" / "any-macos-any"
        runtime = toolchain / "BT3" if bt3_port else toolchain
        self.host_libs = runtime / "Probe"
        self.ios_libs = runtime / "iPhoneOS"
        self.resources = toolchain / "iPhoneOS/Resources"
        source = bt3.ENGINE_SOURCE if bt3_port else ENGINE_ROOT
        super().__init__(source, self.host_libs, "", self.ios_libs, self.ios_libs, str(self.zig))
        self.manifest = json.loads((self.ios_libs / "link.json").read_text(encoding="utf-8"))
        if (self.manifest["schema"], self.manifest["engine"], self.manifest["minimumOS"]) != (
                KIT_SCHEMA, ENGINE_VERSION, MIN_IOS):
            raise BuildError("The bundled iPhoneOS linker inputs do not match this PS2Apple release.")
        self.include = source / "runtime" / "include"
        self.bt3_port = bt3_port
        for path in (self.zig, self.lld, self.darwin_headers / "TargetConditionals.h",
                     self.include / "ps2" / "generated.hpp", self.host_tool("eerecomp"), self.host_tool("vurecomp"),
                     self.resources / "Assets.car", self.resources / "Info.plist",
                     *(self.host_libs / name for name in HOST_LIBS), *(self.ios_libs / name for name in IOS_LIBS),
                     *(self.ios_libs / "Imports" / item["file"] for item in self.manifest["libraries"])):
            if not path.is_file():
                raise BuildError(f"Missing {path.name} from the bundled build environment. Reopen PS2Apple to reinstall it.")

    def host_tool(self, name: str) -> Path:
        return self.host_tools / f"{name}.exe"

    def probe_gameplay(self, gameplay: aot.GameplayProbe | None) -> aot.GameplayProbe | None:
        # Windows checks code coverage with software GS. The IPA still uses Metal.
        return replace(gameplay, metal=False) if gameplay and gameplay.metal else gameplay

    def make_probe(self, directory: Path, generated: Path, gameplay: aot.GameplayProbe | None, *,
                   live_ee: bool = False) -> Path:
        objects = self.compile_units(generated, directory, ios=False)
        # Windows may retain a handle to the prior image briefly after exit.
        executable = directory / f"ps2run-{uuid.uuid4().hex[:8]}.exe"
        response = response_file(directory, [
            *objects, *(self.host_libs / name for name in HOST_LIBS),
            "-lwinmm", "-lshell32", "-lapi-ms-win-core-synch-l1-2-0",
        ])
        run([self.zig, "c++", "-target", WIN_TARGET, "-s", "-Wl,--stack,16777216",
             "-o", executable, f"@{response}"], cwd=directory)
        return executable

    def compile_units(self, generated: Path, directory: Path, *, ios: bool) -> list[Path]:
        directory.mkdir(exist_ok=True)
        objdir = directory / "obj"
        objdir.mkdir(exist_ok=True)
        sources = sorted(generated.glob("*.cpp"))
        if not sources:
            raise BuildError("The selected disc did not produce any AOT code.")
        flags = ["-std=c++20", "-DNDEBUG", "-I", self.include,
                 "-Wno-unused-label", "-Wno-unused-variable"]
        if ios:
            flags += ["-target", IOS_TARGET, "-O3", "-DPS2_AOT_ONLY=1", "-DPS2_GPU",
                      "-isystem", self.darwin_headers]
        else:
            flags += ["-target", WIN_TARGET, "-O2",
                      "-DPS2_AOT_ONLY=1" if self.bt3_port else "-DPS2APPLE_AOT_PROBE=1"]
        commands = []
        objects = []
        # A discovery pass can change only a few units. Keep their unchanged objects
        # within this build, with the generated header included in the cache key.
        headers = hashlib.sha256()
        for header in sorted(generated.glob("*.hpp")):
            headers.update(header.name.encode("utf-8") + b"\0")
            headers.update(header.read_bytes())
        for source in sources:
            obj = objdir / f"{source.stem}.o"
            stamp = obj.with_suffix(".sha256")
            digest = headers.copy()
            digest.update(source.read_bytes())
            key = digest.hexdigest()
            if not (obj.is_file() and stamp.is_file() and stamp.read_text(encoding="ascii") == key):
                commands.append((source, obj, stamp, key))
            objects.append(obj)
        if not commands:
            return objects

        def compile_one(item):
            source, obj, stamp, key = item
            stamp.unlink(missing_ok=True)
            command = [self.zig, "c++", *flags, "-c", source, "-o", obj]
            result = subprocess.run(command, cwd=directory, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace")
            if result.returncode:
                return f"{source.name}: compiler exited with status {result.returncode}.\n{result.stdout[-6000:]}"
            stamp.write_text(key, encoding="ascii")
            return None

        target = "iPhoneOS ARM64" if ios else "Windows boot probe"
        print(f"Compiling {len(commands)} AOT units for {target}.", flush=True)
        with ThreadPoolExecutor(max_workers=aot.JOBS) as executor:
            for index, failure in enumerate(executor.map(compile_one, commands, buffersize=aot.JOBS * 2), 1):
                if failure:
                    raise BuildError(failure)
                if index == 1 or index % 16 == 0 or index == len(commands):
                    detail = f"Compiling {target}: {index}/{len(commands)} units"
                    if ios:
                        progress(.72 + .14 * index / len(commands), detail)
                    else:
                        print(detail, flush=True)
        return objects

    def compile_ios(self, generated: Path, directory: Path) -> Path:
        objects = self.compile_units(generated, directory, ios=True)
        return self.link_ios(directory / "PS2Game", objects, self.ios_libs, IOS_LIBS, self.manifest)

    def link_ios(self, executable: Path, objects: list[Path], libraries: Path,
                 archives: tuple[str, ...], manifest: dict) -> Path:
        inputs = [*objects, *(libraries / name for name in archives)]
        for item in manifest["libraries"]:
            if item["weak"]:
                inputs.append("-weak_library")
            inputs.append(libraries / "Imports" / item["file"])
        directory = executable.parent
        response = response_file(directory, inputs)
        progress(.87, "Linking the iPhoneOS runtime and game code")
        run([self.lld, "-arch", "arm64", "-platform_version", "ios", MIN_IOS, manifest["sdkVersion"],
             "-ObjC", "-dead_strip", "-no_adhoc_codesign", "-o", executable, f"@{response}"], cwd=directory)
        return executable


def response_file(directory: Path, args) -> Path:
    # Both Clang and LLD accept quoted forward-slash paths in response files.
    target = directory / "link.rsp"
    target.write_text("".join(f'"{str(arg).replace(chr(92), "/")}"\n' for arg in args), encoding="utf-8")
    return target


def inspect(iso: Path, elf: Path | None = None) -> dict:
    with ISOImage(iso) as image:
        serial, entry = image.boot()
        if elf is None:
            fingerprint = image.fingerprint(entry, "sha256")
        else:
            image.extract_boot(entry, elf)
            fingerprint = aot.digest(elf)
        port = None
        if port_id := DISC_PROFILES.get(serial):
            candidate = profile_for(BACKEND, port_id)
            algorithm, expected = candidate.fingerprint
            actual = fingerprint
            if algorithm != "sha256":
                actual = image.fingerprint(entry, algorithm) if elf is None else aot.digest(elf, algorithm)
            if actual == expected:
                port = candidate
    title, edition = catalog_disc(serial)
    disc = {"serial": serial, "bootSHA256": fingerprint, "title": title or serial,
            "region": edition.get("region") or "PS2", "bundleID": f"net.isolution.ps2apple.{serial.lower()}",
            "bootSize": entry.size, "channel": "general"}
    if port:
        note = ("Dedicated game port · Boot ELF verified" if port.channel == "tested-pack"
                else "Audited AOT profile · Boot ELF verified")
        disc.update(profile=port.data["id"], bundleID=port.data["bundleID"], channel=port.channel,
                    note=note)
    return disc


def stage_icon(app: Path, resources: Path) -> None:
    for name in ("Assets.car", "PS2AppleAppIcon60x60@2x.png", "PS2AppleAppIcon76x76@2x~ipad.png", "PS2AppleIcon.png"):
        shutil.copyfile(resources / name, app / name)
    info = app / "Info.plist"
    metadata = plistlib.loads(info.read_bytes())
    metadata.update(plistlib.loads((resources / "Info.plist").read_bytes()))
    info.write_bytes(plistlib.dumps(metadata))


def check_tools(kit: LocalKit, directory: Path):
    port = kit.toolchain / "Downhill"
    checks = (
        ("Zig/Clang", [kit.zig, "version"], ZIG_VERSION),
        ("iPhoneOS linker", [kit.lld, "--version"], LLD_VERSION),
        ("Downhill WASI compiler", [port / "WASI/bin/clang.exe", "--version"], downhill.WASI_CLANG_VERSION),
        ("Downhill WASI archiver", [port / "WASI/bin/llvm-ar.exe", "--version"], downhill.WASI_CLANG_VERSION),
        ("Downhill WABT", [port / "WABT/bin/wasm2c.exe", "--version"], downhill.recipe.WABT_VERSION),
        ("Downhill WASM inspector", [port / "WABT/bin/wasm-objdump.exe", "--version"], downhill.recipe.WABT_VERSION),
    )
    for label, command, version in checks:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=30, check=True)
        if version not in result.stdout:
            raise BuildError(f"The bundled {label} did not report the expected version.")
        event("check", key=label, ok=True, detail=result.stdout.strip())
    source = directory / "check.cpp"
    obj = directory / "check.o"
    source.write_text('#include "ps2/generated.hpp"\nint main() { return 0; }\n', encoding="ascii")
    run([kit.zig, "c++", "-target", IOS_TARGET, "-isystem", kit.darwin_headers,
         "-std=c++20", "-I", kit.include, "-c", source, "-o", obj], cwd=directory)
    with obj.open("rb") as stream:
        check_macho(stream, MH_OBJECT)
    event("check", key="iPhoneOS ARM64", ok=True, detail="The local compiler produced a valid iPhoneOS object.")


def build(kit: LocalKit, iso: Path, destination: Path, stage: Path, expected: str | None) -> Path:
    progress(.03, "Reading SYSTEM.CNF and extracting the boot ELF")
    elf = stage / "boot.elf"
    profile = inspect(iso, elf)
    if expected and profile["bootSHA256"] != expected:
        raise BuildError("The selected ISO changed. Choose the disc again.")
    event("disc", **profile, ok=True)
    port_name = profile.get("profile")
    if port_name == "downhill":
        port = profile_for(BACKEND, "downhill").data
        app = downhill.build(kit, elf, stage, port)
    elif port_name == "budokai-tenkaichi-3":
        port = profile_for(BACKEND, "budokai-tenkaichi-3").data
        tools = LocalKit(BACKEND.parent, bt3_port=True)
        generated = bt3.prepare_game_code(tools, iso, elf, stage, make_probe=tools.make_probe)
        progress(.72, "Compiling the dedicated Budokai Tenkaichi 3 port")
        executable = tools.compile_ios(generated, stage / "iPhoneOS")
        app = stage / port["bundle"]
        bt3.app_bundle(tools, executable, app, port)
    if port_name in ("downhill", "budokai-tenkaichi-3"):
        stage_icon(app, kit.resources)
        name = "DownhillRevived" if port_name == "downhill" else "Budokai-Tenkaichi-3"
        target = destination / f"{name}-{profile['serial']}-{uuid.uuid4().hex[:8]}.ipa"
        progress(.94, "Packaging and verifying the dedicated game launcher")
        export_ipa(app, target, port["bundleID"], frontend=port.get("frontend", "shared"))
        return target
    disc_id = (profile["serial"], profile["bootSHA256"])
    audited_preview = port_name == "god-of-war-ii"
    general_probe = aot.uses_general_probe(disc_id, audited_preview=audited_preview)
    progress(.28, "Translating the selected game on this PC")
    generated, report = aot.prepare_game_code(
        kit, iso, elf, stage, boot_entries=aot.KNOWN_BOOT_ENTRIES.get(disc_id, ()),
        audited_preview=audited_preview, learn_ee=disc_id in aot.EE_RECORDING_DISCS,
        general_probe=general_probe, disc_id=disc_id,
    )
    progress(.72, "Compiling the game's iPhoneOS ARM64 code")
    executable = kit.compile_ios(generated, stage / "iPhoneOS")
    progress(.90, "Packaging the iPhoneOS launcher and verifying the IPA")
    app = stage / "PS2Game.app"
    shutil.copytree(kit.resources, app)
    info_path = app / "Info.plist"
    with info_path.open("rb") as stream:
        icon = plistlib.load(stream)
    with info_path.open("wb") as stream:
        plistlib.dump({**aot.bundle_info(profile, general_probe=general_probe), **icon}, stream)
    shutil.move(executable, app / "PS2Game")
    (app / "PortSource.txt").write_text(aot.bundle_report(report), encoding="utf-8")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", profile["title"]).strip("-")[:65] or "PS2Game"
    name = f"{slug}-{profile['serial']}-{uuid.uuid4().hex[:8]}.ipa"
    target = destination / name
    export_ipa(app, target, profile["bundleID"])
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("identify", "check", "build"))
    parser.add_argument("--iso", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--expected")
    args = parser.parse_args()
    try:
        if args.action in ("identify", "build") and args.iso is None:
            parser.error(f"{args.action} requires --iso")
        if args.action == "identify":
            event("disc", **inspect(args.iso.resolve()), ok=True)
            return 0
        if args.action == "build" and args.output is None:
            parser.error("build requires --output")
        kit = LocalKit(BACKEND.parent)
        jobs = args.workspace.resolve() / "Build"
        jobs.mkdir(parents=True, exist_ok=True)
        os.environ["ZIG_GLOBAL_CACHE_DIR"] = str(jobs / "CompilerCache")
        stage = Path(tempfile.mkdtemp(prefix="job-", dir=jobs))
        target = None
        try:
            os.environ["ZIG_LOCAL_CACHE_DIR"] = str(stage / "Cache")
            if args.action == "check":
                check_tools(kit, stage)
            else:
                target = build(kit, args.iso.resolve(), args.output.resolve(), stage, args.expected)
        finally:
            for attempt in range(4):
                try:
                    shutil.rmtree(stage)
                    break
                except OSError:
                    if attempt == 3:
                        print(f"Windows kept temporary files open: {stage}", flush=True)
                    else:
                        time.sleep(.25 * (attempt + 1))
        if target is not None:
            progress(1, "IPA built locally")
            event("artifact", path=str(target))
        return 0
    except (BuildError, DiscError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        event("error", detail=str(error))
        return 1


if __name__ == "__main__":
    sys.exit(main())
