#!/usr/bin/env python3
"""Build the pinned Shaolin Monks game and iPhoneOS runtime into an app bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent.parent))
from aot_deps import engine_source, prepare_wgpu  # noqa: E402
from builder import BuildError, MIN_IOS, TOOLS, cancelled, checkout, output, progress, run  # noqa: E402
from ios_deps import IOS_GAMEPAD_INFO, SDL_REV, prepare_sdl, sdl_link_flags, xcode_cmake  # noqa: E402
from ipa import check_macho  # noqa: E402
from engine_source import ENGINE_REVISION, ENGINE_VERSION, RELEASE_VERSION  # noqa: E402
from prepare import GAME, generate, prepare_compiler, prepare_ios_iop  # noqa: E402

RAYLIB_REV = "c1ab645ca298a2801097931d1079b10ff7eb9df8"
SSE2NEON_REV = "92f6de174717aef09033ad21568d5bb9e5470404"
FFMPEG_REV = "db69d06eeeab4f46da15030a80d539efb4503ca8"
JOBS = max(1, min(4, os.cpu_count() or 1))


@dataclass(frozen=True)
class IOSDependencies:
    sdk: str
    key: str
    sdl: Path
    raylib: Path
    sse2neon: Path
    ffmpeg: Path
    ffmpeg_source: Path
    graphics_source: Path
    graphics_library: Path
    wgpu: Path


def prepare_ffmpeg(source: Path, target: Path, sdk: str):
    libraries = [target / f"lib/lib{name}.a" for name in ("avcodec", "avutil", "swscale")]
    if all(lib.is_file() for lib in libraries) and (target / "include/libavcodec/avcodec.h").is_file():
        return
    print("Building the MPEG-2 decoder and pixel converter for iPhoneOS.", flush=True)
    # FFmpeg's makefiles require build and source paths without whitespace.
    with tempfile.TemporaryDirectory(prefix="ps2apple-ffmpeg-", dir="/tmp") as temporary:
        stage = Path(temporary)
        (stage / "src").symlink_to(source, target_is_directory=True)
        (stage / "sdk").symlink_to(sdk, target_is_directory=True)
        work = stage / "build"
        work.mkdir()
        compiler = stage / "clang"
        command = ["/usr/bin/xcrun", "--sdk", "iphoneos", "clang", "-target", f"arm64-apple-ios{MIN_IOS}",
                   "-isysroot", str(stage / "sdk")]
        compiler.write_text(f"#!/bin/sh\nexec {shlex.join(command)} \"$@\"\n", encoding="utf-8")
        compiler.chmod(0o755)
        prefix = stage / "install"
        run([stage / "src/configure", f"--prefix={prefix}", "--target-os=darwin", "--arch=aarch64",
             "--enable-cross-compile", f"--cc={compiler}", "--ar=/usr/bin/ar", "--ranlib=/usr/bin/ranlib",
             "--nm=/usr/bin/nm", "--disable-everything", "--disable-programs", "--disable-doc",
             "--disable-debug", "--disable-network", "--disable-autodetect", "--disable-asm", "--disable-hwaccels",
             "--disable-avdevice", "--disable-avfilter", "--disable-avformat", "--disable-swresample",
             "--disable-postproc", "--enable-avcodec", "--enable-avutil", "--enable-swscale",
             "--enable-decoder=mpeg2video", "--enable-parser=mpegvideo", "--enable-static", "--disable-shared"], cwd=work)
        run(["/usr/bin/make", "-j", str(JOBS)], cwd=work)
        run(["/usr/bin/make", "install"], cwd=work)
        if not all((prefix / "lib" / lib.name).is_file() for lib in libraries):
            raise BuildError("FFmpeg did not produce the required iPhoneOS libraries.")
        with tempfile.TemporaryDirectory(prefix="ffmpeg-install-", dir=target.parent) as install:
            staged = Path(install) / "prefix"
            shutil.copytree(prefix, staged)
            if target.exists():
                shutil.rmtree(target)
            staged.rename(target)


def prepare_dependencies(cache: Path) -> IOSDependencies:
    sdk = output(["/usr/bin/xcrun", "--sdk", "iphoneos", "--show-sdk-path"])
    xcode = output(["/usr/bin/xcodebuild", "-version"])
    key = hashlib.sha256(f"{sdk}|{xcode}|{MIN_IOS}".encode()).hexdigest()[:12]
    tools = cache / "Toolchains"
    tools.mkdir(exist_ok=True)
    raylib = tools / "Raylib-5.5"
    sse2neon = tools / "sse2neon-1.9.1"
    ffmpeg_source = tools / "FFmpeg-7.1.1-source"
    ffmpeg = tools / f"FFmpeg-7.1.1-ios-{key}"
    checkout(raylib, "https://github.com/raysan5/raylib.git", RAYLIB_REV)
    checkout(sse2neon, "https://github.com/DLTcollab/sse2neon.git", SSE2NEON_REV)
    checkout(ffmpeg_source, "https://github.com/FFmpeg/FFmpeg.git", FFMPEG_REV)
    sdl = prepare_sdl(tools, sdk, key, JOBS)
    prepare_ffmpeg(ffmpeg_source, ffmpeg, sdk)
    graphics_cache = cache.parent.parent / "generic" / ENGINE_REVISION
    shader_tools = graphics_cache / "Toolchains"
    shader_tools.mkdir(parents=True, exist_ok=True)
    graphics_source = engine_source()
    wgpu = prepare_wgpu(shader_tools)
    graphics_key = hashlib.sha256(f"{graphics_source}|{ENGINE_VERSION}|{key}".encode()).hexdigest()[:12]
    graphics_build = graphics_cache / f"GS-iPhoneOS-{graphics_key}"
    graphics_library = graphics_build / "libps2runtime.a"
    if not graphics_library.is_file() or not (graphics_build / "build.ninja").is_file():
        run([TOOLS["cmake"], "-S", graphics_source, "-B", graphics_build, *xcode_cmake(sdk),
             "-DPS2_AOT_ONLY=ON", "-DCMAKE_DISABLE_FIND_PACKAGE_SDL2=ON", f"-DPS2_WGPU_DIR={wgpu}"])
    run([TOOLS["cmake"], "--build", graphics_build, "--target", "ps2runtime", "--parallel", str(JOBS)])
    return IOSDependencies(sdk, key, sdl, raylib, sse2neon, ffmpeg, ffmpeg_source,
                           graphics_source, graphics_library, wgpu)


def apply_ios_patches(source: Path):
    patches = [ROOT / name for name in ("ios-runtime-compat.patch", "ios-stream-render.patch",
                                        "ios-stable-scanout.patch", "ios-field-presentation.patch",
                                        "ios-metal-gs.patch", "ios-gs-depth-filter.patch")]
    # Later patches change context shared with older ones. Find the newest applied patch first.
    for index in range(len(patches) - 1, -1, -1):
        result = subprocess.run(["git", "apply", "--reverse", "--check", patches[index]],
                                cwd=source, capture_output=True)
        if result.returncode == 0:
            patches = patches[index + 1:]
            break
    for patch in patches:
        run(["git", "apply", "--check", patch], cwd=source)
        run(["git", "apply", "--whitespace=nowarn", patch], cwd=source)


def compile_game(source: Path, generated: Path, iop: Path, build: Path, deps: IOSDependencies, boot_hash: str):
    apply_ios_patches(source)
    flags = sdl_link_flags(deps.sdl / "lib/pkgconfig/sdl3.pc")
    if not flags:
        raise BuildError("SDL3's iPhoneOS framework link flags are missing.")
    options = {
        "SOURCE": source,
        "GENERATED": generated,
        "IOP": iop,
        "RAYLIB": deps.raylib,
        "SSE2NEON": deps.sse2neon,
        "FFMPEG": deps.ffmpeg,
        "SDL": deps.sdl / "lib/libSDL3.a",
        "SDL_INCLUDE": deps.sdl / "include",
        "SDL_FLAGS": ";".join(flags),
        "SHARED_IOS": ROOT.parent.parent / "ios",
        "GRAPHICS_SOURCE": deps.graphics_source,
        "GRAPHICS_LIBRARY": deps.graphics_library,
        "WGPU": deps.wgpu,
        "BOOT_HASH": boot_hash,
    }
    run([TOOLS["cmake"], "-S", ROOT / "ios", "-B", build, *xcode_cmake(deps.sdk),
         *[f"-DPS2APPLE_{key}={value}" for key, value in options.items()]])
    run([TOOLS["cmake"], "--build", build, "--target", "ShaolinMonks", "--parallel", "2"])
    executable = build / "ShaolinMonks.app/ShaolinMonks"
    with executable.open("rb") as stream:
        check_macho(stream)
    return executable


def app_bundle(source: Path, executable: Path, app: Path, profile: dict, deps: IOSDependencies):
    app.mkdir()
    name = app.stem
    shutil.copy2(executable, app / name)
    (app / name).chmod(0o755)
    landscape = ["UIInterfaceOrientationLandscapeLeft", "UIInterfaceOrientationLandscapeRight"]
    info = {
        **IOS_GAMEPAD_INFO,
        "CFBundleDisplayName": "Shaolin Monks", "CFBundleName": name, "CFBundleExecutable": name,
        "CFBundleIdentifier": profile["bundleID"], "CFBundlePackageType": "APPL",
        "CFBundleInfoDictionaryVersion": "6.0", "CFBundleShortVersionString": RELEASE_VERSION,
        "CFBundleVersion": "1407", "CFBundleSupportedPlatforms": ["iPhoneOS"],
        "MinimumOSVersion": MIN_IOS, "LSRequiresIPhoneOS": True, "UIDeviceFamily": [1, 2],
        "UIRequiredDeviceCapabilities": ["arm64", "metal"], "UIFileSharingEnabled": True,
        "LSSupportsOpeningDocumentsInPlace": True, "UILaunchScreen": {}, "UIRequiresFullScreen": True,
        "UISupportedInterfaceOrientations": landscape, "UISupportedInterfaceOrientations~ipad": landscape,
        "UIApplicationSceneManifest": {
            "UIApplicationSupportsMultipleScenes": False,
            "UISceneConfigurations": {"UIWindowSceneSessionRoleApplication": [{
                "UISceneConfigurationName": "SDLSceneConfiguration", "UISceneDelegateClassName": "SDLUIKitSceneDelegate"
            }]},
        },
    }
    with (app / "Info.plist").open("wb") as stream:
        plistlib.dump(info, stream)
    shutil.copy2(source / "LICENSE", app / "LICENSE-MKSM.txt")
    shutil.copy2(deps.graphics_source / "LICENSE", app / "LICENSE-ps2-native-recomp.txt")
    for name in ("wgpu-Apache.txt", "wgpu-MIT.txt"):
        shutil.copy2(ROOT.parent / "generic" / "ios" / "Licenses" / name, app / name)
    shutil.copy2(deps.raylib / "LICENSE", app / "LICENSE-Raylib.txt")
    shutil.copy2(deps.ffmpeg_source / "COPYING.LGPLv2.1", app / "LICENSE-FFmpeg.txt")
    (app / "PortSource.txt").write_text(
        f"https://github.com/{profile['upstream']}/tree/{profile['revision']}\n"
        f"https://github.com/libsdl-org/SDL/tree/{SDL_REV}\n"
        f"https://github.com/raysan5/raylib/tree/{RAYLIB_REV}\n"
        f"https://github.com/DLTcollab/sse2neon/tree/{SSE2NEON_REV}\n"
        f"https://github.com/FFmpeg/FFmpeg/tree/{FFMPEG_REV}\n"
        "The PS2Apple source contains the iPhoneOS host and the build recipe.\n", encoding="utf-8"
    )


def main() -> int:
    signal.signal(signal.SIGTERM, cancelled)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "build"))
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--iso", type=Path)
    parser.add_argument("--elf", type=Path)
    parser.add_argument("--app", type=Path)
    args = parser.parse_args()
    if args.action == "build" and not all((args.iso, args.elf, args.app)):
        parser.error("build requires --iso, --elf and --app")
    try:
        profile = json.loads(args.profile.read_text(encoding="utf-8"))
        cache = args.cache.resolve()
        cache.mkdir(parents=True, exist_ok=True)
        source = cache / "Source"
        checkout(source, f"https://github.com/{profile['upstream']}.git", profile["revision"])
        compiler = prepare_compiler(source, cache / "Compiler")
        deps = prepare_dependencies(cache)
        iop = prepare_ios_iop(source, cache / f"IOP-iPhoneOS-{deps.key}")
        if args.action == "prepare":
            return 0
        boot_hash = profile["bootSHA256"]
        with args.elf.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != boot_hash:
                raise BuildError("The extracted boot executable no longer matches the supported disc.")
        workspace = cache / f"NativeSources-{boot_hash[:16]}"
        if not workspace.is_dir():
            progress(.36, "Generating the game code for the native build")
            generate(source, compiler, iop, args.elf.resolve(), workspace, profile)
        manifest = json.loads((workspace / "PS2Apple-Port.json").read_text(encoding="utf-8"))
        if manifest.get("bootSHA256") != boot_hash or manifest.get("revision") != profile["revision"]:
            raise BuildError("The cached game code does not match this disc revision.")
        progress(.51, "Compiling and linking Shaolin Monks for iPhoneOS ARM64")
        executable = compile_game(workspace, workspace / GAME / "runtime/output", iop,
                                  cache / f"NativeBuild-{deps.key}", deps, boot_hash)
        progress(.87, "Staging the verified Shaolin Monks app bundle")
        app_bundle(workspace, executable, args.app, profile, deps)
    except (BuildError, OSError, ValueError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
