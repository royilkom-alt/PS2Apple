#!/usr/bin/env python3
"""Build the USA Budokai Tenkaichi 3 pack with its pinned iPhoneOS runtime."""
from __future__ import annotations

import argparse
import binascii
import hashlib
import json
import os
import plistlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent.parent))
from aot_deps import WGPU_VERSION, prepare_wgpu  # noqa: E402
from builder import BuildError, MIN_IOS, TOOLS, cancelled, output, progress, run  # noqa: E402
from disc import DiscError, ISOImage  # noqa: E402
from engine_source import RELEASE_VERSION  # noqa: E402
from ios_deps import IOS_GAMEPAD_INFO, SDL_REV, prepare_sdl, sdl_link_flags, xcode_cmake  # noqa: E402
from ipa import check_macho  # noqa: E402
from ports.generic import build as generic  # noqa: E402  (bundle_dylibs, lanceur et constantes macOS)
ENGINE_SOURCE = ROOT / "engine"
ENGINE_BASE = "e9a48aae8e0537f3a91d8584b2d78c6056723435"
BT3_REFERENCE = "https://github.com/z3xox/BT3-Recomp/tree/e22a34beb564e2d0f32bd60b02f0a95d9645ddcc"

JOBS = max(1, min(4, os.cpu_count() or 1))
BOOT_FRAMES = 180
DISCOVERY_ROUNDS = 5
MISS = re.compile(r"(?:[a-zA-Z0-9_]+:)?[a-fA-F0-9]{1,8}")
BT3_BOOT = "811188ba9b416500d921cd4d9514df0cbf42f3a41a99cf5aac5a3da37171bf99"
BT3_BASE = 0x334C00


@dataclass(frozen=True)
class Dependencies:
    source: Path
    host_tools: Path
    sdk: str
    sdl: Path
    wgpu: Path
    host_compiler: str
    desktop_wgpu: Path | None = None


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def host_flags(compiler: str) -> list[str]:
    return ["-G", "Ninja", f"-DCMAKE_MAKE_PROGRAM={TOOLS['ninja']}", "-DCMAKE_BUILD_TYPE=Release",
            f"-DCMAKE_CXX_COMPILER={compiler}", "-DPS2_AOT_ONLY=ON",
            "-DCMAKE_DISABLE_FIND_PACKAGE_SDL2=ON"]


def prepare(cache: Path, *, desktop_metal: bool = False) -> Dependencies:
    cache.mkdir(parents=True, exist_ok=True)
    source = ENGINE_SOURCE
    if not (source / "recomp/main.cpp").is_file():
        raise BuildError("The Budokai Tenkaichi 3 compiler pack is incomplete. Reinstall PS2Apple.")
    print("Using the pinned Budokai Tenkaichi 3 compiler and renderer.", flush=True)
    sdk = output(["/usr/bin/xcrun", "--sdk", "iphoneos", "--show-sdk-path"])
    compiler = output(["/usr/bin/xcrun", "--sdk", "macosx", "--find", "clang++"])
    xcode = output(["/usr/bin/xcodebuild", "-version"])
    key = hashlib.sha256(f"{sdk}|{xcode}|{MIN_IOS}".encode()).hexdigest()[:12]
    tools = cache / "Toolchains"
    tools.mkdir(exist_ok=True)
    host_key = hashlib.sha256(f"{source}|{ENGINE_BASE}|{key}".encode()).hexdigest()[:12]
    host = cache / f"BT3-Compiler-{host_key}"
    if not all((host / name).is_file() for name in ("eerecomp", "vurecomp")):
        run([TOOLS["cmake"], "-S", source, "-B", host, *host_flags(compiler)])
        run([TOOLS["cmake"], "--build", host, "--target", "eerecomp", "vurecomp", "--parallel", str(JOBS)])
    wgpu = prepare_wgpu(tools)
    # The same SDL build can be reused if Shaolin Monks has already prepared it.
    existing_sdl = cache.parent.parent / "shaolin-monks" / "9c014184b54f8f4235db9327bf6a5d77566475c5" / "Toolchains"
    existing_sdl = existing_sdl / f"SDL3-3.4.18-Metal-{key}"
    if all((existing_sdl / name).is_file() for name in ("lib/libSDL3.a", "lib/pkgconfig/sdl3.pc", "include/SDL3/SDL.h")):
        sdl = existing_sdl
    else:
        sdl = prepare_sdl(tools, sdk, key, JOBS)
    # Le build macOS rend le jeu avec la version desktop (Metal) de wgpu-native.
    desktop_wgpu = prepare_wgpu(tools, mac=True) if desktop_metal else None
    return Dependencies(source, host, sdk, sdl, wgpu, compiler, desktop_wgpu)


def seed_bt3_overlay(iso: Path, overlays: Path, entries: Path) -> None:
    """Translate the retail gameplay overlay at the address documented by BT3-Recomp."""
    with ISOImage(iso) as image:
        item = image.find(["BIN", "DBZP.BIN"])
        if item.directory or not 0 < item.size <= (32 << 20) - BT3_BASE:
            raise BuildError("The Budokai Tenkaichi 3 gameplay overlay has an unexpected size.")
        data = image.read(item.offset, item.size)
    stem = f"{BT3_BASE:08X}_{binascii.crc32(data):08X}"
    (overlays / f"{stem}.bin").write_bytes(data)
    offsets = [line for line in (ROOT / "ios" / "bt3-overlay-entries.txt").read_text().splitlines()
               if line and not line.startswith("#")]
    if any(int(offset, 16) + 4 > len(data) for offset in offsets):
        raise BuildError("The Budokai Tenkaichi 3 gameplay overlay is shorter than its entry map.")
    entries.write_text("".join(f"{stem}:{offset}\n" for offset in offsets), encoding="ascii")
    print(f"BT3 gameplay overlay at 0x{BT3_BASE:X} · {len(offsets)} entry points", flush=True)


def seed_bt3_vu1(boot: bytes, recordings: Path, manifest: dict) -> int:
    """Recover known VU1 programs and entry points from the matching retail ELF."""
    if hashlib.sha256(boot).hexdigest() != manifest["elf_sha256"]:
        raise BuildError("The BT3 VU1 map does not match this boot executable.")
    image_size = int(manifest["image_size"], 16)
    count = 0
    for program in manifest["programs"]:
        chunks = []
        for segment in program["segments"]:
            offset, length = int(segment["offset"], 16), int(segment["length"], 16)
            if offset < 0 or length <= 0 or offset + length > len(boot):
                raise BuildError(f"BT3 VU1 program {program['id']} points outside the boot executable.")
            chunks.append(boot[offset:offset + length])
        code = b"".join(chunks)
        key = 1469598103934665603
        for byte in code:
            key = ((key ^ byte) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
        if len(code) != int(program["extent"], 16) or len(code) > image_size or key != int(program["fnv1a64"], 16):
            raise BuildError(f"BT3 VU1 program {program['id']} failed content validation.")
        for address in program["entries"]:
            entry = int(address, 16)
            if entry < 0 or entry + 8 > len(code) or entry % 8:
                raise BuildError(f"BT3 VU1 program {program['id']} has an invalid entry point.")
            (recordings / f"seed_{program['id']}_{entry:04X}.bin").write_bytes(code.ljust(image_size, b"\0"))
            count += 1
    return count


def translate(deps: Dependencies, elf: Path, generated: Path, entries: Path, modules: Path, overlays: Path):
    with tempfile.TemporaryDirectory(prefix="translate-", dir=generated.parent) as temporary:
        stage = Path(temporary)
        run([deps.host_tools / ("eerecomp.exe" if os.name == "nt" else "eerecomp"), elf, "--entries", entries, "--modules", modules,
             "--overlays", overlays, "--out", stage])
        if not (stage / "table.cpp").is_file() or not any(stage.glob("unit_*.cpp")):
            raise BuildError("The static translator did not produce a boot function table.")
        for path in generated.iterdir():
            if not (stage / path.name).exists():
                path.unlink()
        for path in stage.iterdir():
            previous = generated / path.name
            if not previous.is_file() or previous.read_bytes() != path.read_bytes():
                path.replace(previous)


def probe(executable: Path, iso: Path, stage: Path, windows: bool) -> tuple[bool, str]:
    log = stage / "probe.log"
    env = dict(os.environ, PS2_EE_LIVE="0", PS2_VU_LIVE="0", PS2_GS="cpu",
               PS2_VU_RECORD=str(stage / "vu1"), PS2_VU0_RECORD=str(stage / "vu0"))
    if windows:
        env["PS2_DUMP"] = str(stage / "ee-ram.bin")
    card = stage / "probe.ps2"
    card.unlink(missing_ok=True)
    # Boot from SYSTEM.CNF as the IPA does. Passing the extracted file as argv[0]
    # would tell the guest its program is cdrom0:\BOOT.ELF;1.
    command = [executable, "--disc", iso, "--game-dir", stage, "--frames", str(BOOT_FRAMES),
               "--misses", stage / "misses.txt", "--memcard", card,
               "--press", "60:start", "--press", "110:cross"]
    print(f"Booting from SYSTEM.CNF for {BOOT_FRAMES} guest frames.", flush=True)
    with log.open("wb") as stream:
        try:
            result = subprocess.run(command, env=env, cwd=stage, stdout=stream, stderr=subprocess.STDOUT,
                                    timeout=120)
        except subprocess.TimeoutExpired as error:
            raise BuildError("The desktop AOT boot probe timed out before reaching its frame limit.") from error
    with log.open("rb") as stream:
        stream.seek(max(0, log.stat().st_size - 32768))
        lines = stream.read().decode("utf-8", errors="replace").splitlines()
    if result.returncode:
        print("\n".join(lines[-20:]), flush=True)
        raise BuildError(f"The desktop AOT boot probe exited with status {result.returncode}.")
    reason = next((line for line in reversed(lines) if line.startswith("[ee] stopped after ")), "")
    if reason.startswith(f"[ee] stopped after {BOOT_FRAMES} frames,") and reason.endswith(": frame limit reached"):
        print(f"Desktop AOT boot reached {BOOT_FRAMES} guest frames.", flush=True)
        return True, reason
    print("\n".join(lines[-18:]), flush=True)
    return False, reason or "The boot probe did not report a complete run."


def prepare_game_code(deps: Dependencies, iso: Path, elf: Path, stage: Path, *, make_probe=None) -> Path:
    generated, overlays, modules = (stage / name for name in ("Generated", "Overlays", "Modules"))
    for directory in (generated, overlays, modules, stage / "vu0", stage / "vu1"):
        directory.mkdir()
    entries, misses = stage / "entries.txt", stage / "misses.txt"
    boot = elf.read_bytes()
    seed_bt3_overlay(iso, overlays, entries)
    manifest = json.loads((ROOT / "ios" / "bt3-vu1.json").read_text())
    count = seed_bt3_vu1(boot, stage / "vu1", manifest)
    print(f"BT3 VU1 · {len(manifest['programs'])} validated microprograms · {count} entry points", flush=True)
    if b"SNR2" in boot or b"xff2" in boot:
        run([sys.executable, deps.source / "scripts/disc.py", "modules", iso, modules])
    window_tool = deps.source / "scripts/windows.py"
    has_windows = bool(output([sys.executable, window_tool, "list", elf]))
    host = stage / "Desktop Probe"
    for attempt in range(DISCOVERY_ROUNDS):
        progress(.37 + .05 * attempt, f"Translating and checking the game boot ({attempt + 1}/{DISCOVERY_ROUNDS})")
        translate(deps, elf, generated, entries, modules, overlays)
        if make_probe:
            executable = make_probe(host, generated, None)
        else:
            if attempt == 0:
                run([TOOLS["cmake"], "-S", deps.source, "-B", host, *host_flags(deps.host_compiler),
                     f"-DPS2_GENERATED={generated}"])
            run([TOOLS["cmake"], "--build", host, "--target", "ps2run", "--parallel", str(JOBS)])
            executable = host / "ps2run"
        misses.unlink(missing_ok=True)
        success, reason = probe(executable, iso, stage, has_windows)
        if success:
            break
        if ": missing code at " not in reason or not misses.is_file():
            raise BuildError(f"Cannot boot this disc with the automatic runtime. {reason}")
        if has_windows and (stage / "ee-ram.bin").is_file():
            run([sys.executable, window_tool, "capture", elf, stage / "ee-ram.bin", overlays, misses])
        old = set(entries.read_text().split())
        new = {token for token in misses.read_text().split() if MISS.fullmatch(token)} - old
        if not new or attempt + 1 == DISCOVERY_ROUNDS:
            raise BuildError(f"The disc needs more static entry points or a port adapter. {reason}")
        with entries.open("a", encoding="ascii") as stream:
            stream.writelines(token + "\n" for token in sorted(new))
        print(f"Discovered {len(new)} guest entry points. Compiling them ahead of time.", flush=True)
    if any((stage / "vu1").glob("*.bin")) or any((stage / "vu0").glob("*.bin")):
        progress(.65, "Compiling VU programs from the disc and boot probe")
        run([deps.host_tools / ("vurecomp.exe" if os.name == "nt" else "vurecomp"), stage / "vu1",
             generated, "--vu0-dir", stage / "vu0"])
    return generated


def compile_ios(deps: Dependencies, generated: Path, build: Path, *, windows_kit: bool = False) -> Path:
    flags = sdl_link_flags(deps.sdl / "lib/pkgconfig/sdl3.pc")
    if not flags:
        raise BuildError("SDL3 is missing the iPhoneOS framework link flags.")
    config = {"IOS_HOST": ROOT / "ios", "SHARED_IOS": ROOT.parent.parent / "ios", "SDL": deps.sdl / "lib/libSDL3.a",
              "SDL_INCLUDE": deps.sdl / "include", "SDL_FLAGS": ";".join(flags)}
    run([TOOLS["cmake"], "-S", deps.source, "-B", build, *xcode_cmake(deps.sdk),
         "-DPS2_AOT_ONLY=ON", "-DCMAKE_DISABLE_FIND_PACKAGE_SDL2=ON",
         f"-DPS2APPLE_WINDOWS_KIT={'ON' if windows_kit else 'OFF'}",
         f"-DPS2_GENERATED={generated}", f"-DPS2_WGPU_DIR={deps.wgpu}",
         *[f"-DPS2APPLE_{key}={value}" for key, value in config.items()]])
    targets = ["PS2Game", "ps2apple_ios_launcher"] if windows_kit else ["PS2Game"]
    run([TOOLS["cmake"], "--build", build, "--target", *targets, "--parallel", str(JOBS)])
    executable = build / "PS2Game.app" / "PS2Game"
    with executable.open("rb") as stream:
        check_macho(stream)
    return executable


def compile_mac(deps: Dependencies, generated: Path, build: Path) -> Path:
    """Compile le code AOT de BT3 en programme macOS ARM64 fenêtré (SDL2 + Metal), avec le moteur dédié."""
    if deps.desktop_wgpu is None:
        raise BuildError("The macOS build needs the desktop Metal shader runtime.")
    if not (generic.HOMEBREW / "lib/cmake/SDL2/SDL2Config.cmake").is_file():
        raise BuildError("The macOS build needs SDL2. Run: brew install sdl2")
    run([TOOLS["cmake"], "-S", deps.source, "-B", build, "-G", "Ninja", f"-DCMAKE_MAKE_PROGRAM={TOOLS['ninja']}",
         "-DCMAKE_BUILD_TYPE=Release", f"-DCMAKE_CXX_COMPILER={deps.host_compiler}",
         "-DCMAKE_OSX_ARCHITECTURES=arm64", f"-DCMAKE_OSX_DEPLOYMENT_TARGET={generic.MAC_MIN_OS}",
         # Comme pour le port générique : le code non atteint par la sonde est traduit à la volée sur macOS.
         f"-DCMAKE_PREFIX_PATH={generic.HOMEBREW}", "-DPS2_AOT_ONLY=OFF",
         f"-DPS2_GENERATED={generated}", f"-DPS2_WGPU_DIR={deps.desktop_wgpu}"])
    if "PS2_WINDOW" not in (build / "build.ninja").read_text(errors="ignore"):
        raise BuildError("CMake did not find SDL2, so ps2run would have no window.")
    run([TOOLS["cmake"], "--build", build, "--target", "ps2run", "--parallel", str(JOBS)])
    return build / "ps2run"


def mac_app_bundle(deps: Dependencies, executable: Path, app: Path, profile: dict) -> None:
    """Assemble l'app macOS : ps2run + dylibs Homebrew + lanceur partagé, signée ad hoc."""
    contents = app / "Contents"
    macos, resources = contents / "MacOS", contents / "Resources"
    macos.mkdir(parents=True)
    resources.mkdir()
    shutil.copy2(executable, macos / "ps2run")
    generic.bundle_dylibs(macos / "ps2run", contents / "Frameworks")
    run(["/usr/bin/xcrun", "--sdk", "macosx", "clang", "-fobjc-arc", "-O2", "-arch", "arm64",
         f"-mmacosx-version-min={generic.MAC_MIN_OS}", "-framework", "AppKit", "-framework", "UniformTypeIdentifiers",
         generic.MAC_LAUNCHER, "-o", macos / "PS2AppleMac"])
    info = {
        "CFBundleExecutable": "PS2AppleMac", "CFBundleIdentifier": profile["bundleID"] + ".mac",
        "CFBundlePackageType": "APPL", "CFBundleName": profile["title"], "CFBundleDisplayName": profile["title"],
        "CFBundleShortVersionString": RELEASE_VERSION, "CFBundleVersion": "1612",
        "CFBundleSupportedPlatforms": ["MacOSX"], "LSMinimumSystemVersion": generic.MAC_MIN_OS,
        "LSApplicationCategoryType": "public.app-category.games", "NSHighResolutionCapable": True,
        "GCSupportsControllerUserInteraction": True,
        "PS2DiscSerial": profile["serial"], "PS2BootSHA256": profile["bootSHA256"],
    }
    with (contents / "Info.plist").open("wb") as stream:
        plistlib.dump(info, stream)
    (contents / "PkgInfo").write_text("APPL????", encoding="ascii")
    shutil.copy2(deps.source / "LICENSE", resources / "LICENSE-ps2-native-recomp.txt")
    shutil.copytree(ROOT / "ios" / "Licenses", resources / "Licenses")
    notice = deps.source / "runtime/src/ipu/NOTICE"
    if notice.is_file():
        shutil.copy2(notice, resources / "Licenses/Play-MPEG.txt")
    (resources / "PortSource.txt").write_text(
        f"Pinned PS2Apple 1.4.2 runtime: https://github.com/exoteric33/ps2-native-recomp/tree/{ENGINE_BASE}\n"
        f"https://github.com/gfx-rs/wgpu-native/releases/tag/{WGPU_VERSION}\n"
        f"BT3 overlay and VU1 map: {BT3_REFERENCE}\n"
        f"Desktop AOT boot probe: {BOOT_FRAMES} guest frames.\n",
        encoding="utf-8")
    # Signature ad hoc : obligatoire pour exécuter du code ARM64 sur macOS.
    run(["/usr/bin/codesign", "--force", "--deep", "--sign", "-", app])


def app_bundle(deps: Dependencies, executable: Path, app: Path, profile: dict) -> None:
    app.mkdir()
    shutil.copy2(executable, app / "PS2Game")
    landscape = ["UIInterfaceOrientationLandscapeLeft", "UIInterfaceOrientationLandscapeRight"]
    info = {
        **IOS_GAMEPAD_INFO,
        "CFBundleExecutable": "PS2Game", "CFBundleIdentifier": profile["bundleID"], "CFBundlePackageType": "APPL",
        "CFBundleName": profile["title"], "CFBundleDisplayName": profile["title"],
        "CFBundleShortVersionString": RELEASE_VERSION, "CFBundleVersion": "1607",
        "CFBundleSupportedPlatforms": ["iPhoneOS"], "MinimumOSVersion": MIN_IOS,
        "LSRequiresIPhoneOS": True, "UIDeviceFamily": [1, 2], "UIFileSharingEnabled": True,
        "LSSupportsOpeningDocumentsInPlace": True, "UILaunchScreen": {}, "UIRequiresFullScreen": True,
        "UISupportedInterfaceOrientations": landscape, "UISupportedInterfaceOrientations~ipad": landscape,
        "PS2DiscSerial": profile["serial"], "PS2BootSHA256": profile["bootSHA256"],
        "UIApplicationSceneManifest": {
            "UIApplicationSupportsMultipleScenes": False,
            "UISceneConfigurations": {"UIWindowSceneSessionRoleApplication": [{
                "UISceneConfigurationName": "SDLSceneConfiguration", "UISceneDelegateClassName": "SDLUIKitSceneDelegate"
            }]},
        },
    }
    with (app / "Info.plist").open("wb") as stream:
        plistlib.dump(info, stream)
    shutil.copy2(deps.source / "LICENSE", app / "LICENSE-ps2-native-recomp.txt")
    shutil.copytree(ROOT / "ios" / "Licenses", app / "Licenses")
    notice = deps.source / "runtime/src/ipu/NOTICE"
    if notice.is_file():
        shutil.copy2(notice, app / "Licenses/Play-MPEG.txt")
    (app / "PortSource.txt").write_text(
        f"Pinned PS2Apple 1.4.2 runtime: https://github.com/exoteric33/ps2-native-recomp/tree/{ENGINE_BASE}\n"
        f"https://github.com/gfx-rs/wgpu-native/releases/tag/{WGPU_VERSION}\n"
        f"https://github.com/libsdl-org/SDL/tree/{SDL_REV}\n"
        f"BT3 overlay and VU1 map: {BT3_REFERENCE}\n"
        f"Desktop AOT boot probe: {BOOT_FRAMES} guest frames.\n",
        encoding="utf-8")


def main() -> int:
    signal.signal(signal.SIGTERM, cancelled)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "build"))
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--cache", required=True, type=Path)
    for name in ("iso", "elf", "app"):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("--platform", choices=("ios", "mac"), default="ios")
    args = parser.parse_args()
    mac = args.platform == "mac"
    try:
        if args.action == "prepare":
            prepare(args.cache.resolve(), desktop_metal=mac)
            return 0
        if not all((args.profile, args.iso, args.elf, args.app)):
            parser.error("build requires --profile, --iso, --elf and --app")
        profile = json.loads(args.profile.read_text(encoding="utf-8"))
        if profile["serial"] != "SLUS-21678" or profile["bootSHA256"] != BT3_BOOT or digest(args.elf) != BT3_BOOT:
            raise BuildError("This pack requires the verified USA Budokai Tenkaichi 3 boot ELF.")
        deps = prepare(args.cache.resolve(), desktop_metal=mac)
        with tempfile.TemporaryDirectory(prefix="aot-", dir=args.app.parent) as temporary:
            stage = Path(temporary)
            generated = prepare_game_code(deps, args.iso.resolve(), args.elf.resolve(), stage)
            if mac:
                progress(.72, "Linking the game's AOT code with the macOS Metal runtime")
                executable = compile_mac(deps, generated, stage / "macOS")
                progress(.89, "Staging the macOS app and launcher")
                mac_app_bundle(deps, executable, args.app, profile)
                return 0
            progress(.72, "Linking the game's AOT code with the iPhoneOS Metal shader runtime")
            executable = compile_ios(deps, generated, stage / "iPhoneOS")
            progress(.89, "Staging the iPhoneOS app and shared launcher")
            app_bundle(deps, executable, args.app, profile)
        return 0
    except (BuildError, DiscError, OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print(f"ERROR: {error}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
