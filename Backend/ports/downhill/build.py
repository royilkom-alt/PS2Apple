#!/usr/bin/env python3
"""Build the pinned Downhill Revived source and iPhoneOS host."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent.parent))
from builder import BuildError, MIN_IOS, capture, checkout, output, progress, run  # noqa: E402
from engine_source import RELEASE_VERSION  # noqa: E402
from ipa import DOWNHILL_UI, check_macho  # noqa: E402
from ios_deps import IOS_GAMEPAD_INFO, sdl_link_flags  # noqa: E402

WABT_REV = "ff0ef7e0009402740c805a9744c09b05be063e48"
WABT_VERSION = "1.0.42"
WABT_CMAKE_PATCH = ROOT / "wabt-cmake-spaces.patch"
SIMDE_REV = "71fd833d9666141edcd1d3c109a80e228303d8d7"
SIMDE_WASI_PATCH = ROOT / "simde-wasi-float16.patch"
RUSTUP = "/opt/homebrew/opt/rustup/bin/rustup"
RUST_VERSION = "1.97.1"
RUST_TARGET = "aarch64-apple-ios"
WASI_NAME = "wasi-sdk-34.0-arm64-macos"
WASI_URL = f"https://github.com/WebAssembly/wasi-sdk/releases/download/wasi-sdk-34/{WASI_NAME}.tar.gz"
WASI_SHA256 = "9c59398106b417f8f14913380fdf0097a8cc0ff4af9eb3ce0065a859e88d49e9"
PATCH = ROOT / "ios-arm64.patch"
WASM_BUILD_PATCH = ROOT / "wasm-link-fail-fast.patch"
IOS_RUNTIME_PATCH = ROOT / "ios-memory-startup.patch"
IOS_LAUNCHER_PATCH = ROOT / "ios-prelaunch.patch"
IOS_CATALOG_PATCH = ROOT / "ios-catalog-controls.patch"
IOS_PACK_PATCH = ROOT / "ios-pack-launcher.patch"
IOS_TOUCH_PATCH = ROOT / "ios-touch-overlay.patch"
IOS_IDLE_BLUETOOTH_PATCH = ROOT / "ios-idle-bluetooth.patch"
IOS_DISC_PICKER_PATCH = ROOT / "ios-disc-picker.patch"
IOS_BRANDING_PATCH = ROOT / "ios-launcher-branding.patch"
SHARED_IOS = ROOT.parent.parent / "ios"
PORT_PATCHES = (PATCH, WASM_BUILD_PATCH, IOS_RUNTIME_PATCH, IOS_LAUNCHER_PATCH, IOS_CATALOG_PATCH,
                IOS_PACK_PATCH, IOS_TOUCH_PATCH, IOS_IDLE_BLUETOOTH_PATCH, IOS_DISC_PICKER_PATCH, IOS_BRANDING_PATCH)
JOBS = min(4, os.cpu_count() or 2)


def cmake_install(source: Path, target: Path, options: dict, env: dict):
    with tempfile.TemporaryDirectory(prefix="cmake-", dir=target.parent) as temporary:
        work = Path(temporary) / "build"
        prefix = Path(temporary) / "install"
        run(["cmake", "-S", source, "-B", work, "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
             f"-DCMAKE_INSTALL_PREFIX={prefix}", "-DCMAKE_INSTALL_LIBDIR=lib",
             *[f"-D{key}={value}" for key, value in options.items()]], env=env)
        run(["cmake", "--build", work, "--parallel", str(JOBS)], env=env)
        run(["cmake", "--install", work], env=env)
        if target.exists():
            # A partial installation is isolated to the builder's own cache.
            shutil.rmtree(target)
        prefix.rename(target)


def wasi_sdk(cache: Path) -> Path:
    sdk = cache / WASI_NAME
    if not (sdk / "bin/clang").is_file():
        with tempfile.TemporaryDirectory(prefix="wasi-", dir=cache) as temporary:
            archive = Path(temporary) / "wasi.tar.gz"
            run(["/usr/bin/curl", "--proto", "=https", "--tlsv1.2", "--fail", "--location", "--retry", "2",
                 "--connect-timeout", "30", WASI_URL, "--output", archive])
            with archive.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != WASI_SHA256:
                raise BuildError(f"WASI SDK SHA-256 did not match the pinned release. Received {digest}.")
            with tarfile.open(archive) as tar:
                tar.extractall(temporary, filter="data")
            fresh = Path(temporary) / WASI_NAME
            if not (fresh / "share/wasi-sysroot").is_dir():
                raise BuildError("WASI SDK archive has an unexpected layout.")
            if sdk.exists():
                shutil.rmtree(sdk)
            fresh.rename(sdk)
    return sdk


def apply_tool_patch(source: Path, patch: Path):
    # Accept a previously patched checkout, or apply the patch to the existing pinned source.
    patched = subprocess.run(["git", "apply", "--reverse", "--check", patch],
                             cwd=source, capture_output=True)
    if patched.returncode:
        run(["git", "apply", patch], cwd=source)


def prepare_port_source(source: Path, profile: dict):
    patches = PORT_PATCHES
    checkout(source, f"https://github.com/{profile['upstream']}.git", profile["revision"])
    # An isolated Git index validates overlapping patches without changing the cached files
    # or its real index. An interrupted upgrade can resume at any complete patch boundary.
    names = set()
    for patch in patches:
        stats = output(["git", "apply", "--numstat", patch], cwd=source)
        names.update(line.split("\t", 2)[2] for line in stats.splitlines())
    tracked = set(output(["git", "ls-tree", "-r", "--name-only", "HEAD", "--", *sorted(names)],
                         cwd=source).splitlines())
    present = sorted(name for name in names if name in tracked or (source / name).exists())
    with tempfile.TemporaryDirectory(prefix="patch-check-", dir=source.parent) as temporary:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(temporary) / "index"))
        for count in range(len(patches), -1, -1):
            output(["git", "read-tree", "HEAD"], cwd=source, env=env)
            if present:
                output(["git", "add", "--force", "--all", "--", *present], cwd=source, env=env)
            for patch in reversed(patches[:count]):
                check = subprocess.run(["git", "apply", "--cached", "--reverse", str(patch)],
                                       cwd=source, env=env, capture_output=True)
                if check.returncode:
                    break
            else:
                clean = subprocess.run(["git", "diff", "--cached", "--quiet", "HEAD"],
                                       cwd=source, env=env, capture_output=True)
                if clean.returncode == 0:
                    for patch in patches[count:]:
                        run(["git", "apply", patch], cwd=source)
                    return
    raise BuildError(f"The cached port source was modified in {source}. Move it aside and retry.")


def wabt_tool(cache: Path, env: dict) -> Path:
    install = cache / f"wabt-{WABT_VERSION}"
    wasm2c = install / "bin/wasm2c"
    valid = wasm2c.is_file() and (install / "share/wabt/wasm2c/wasm-rt-impl.c").is_file()
    if valid:
        result = capture([wasm2c, "--version"])
        if result.returncode == 0 and result.stdout.strip() == WABT_VERSION:
            return install
    source = cache / "wabt-source"
    checkout(source, "https://github.com/WebAssembly/wabt.git", WABT_REV)
    apply_tool_patch(source, WABT_CMAKE_PATCH)
    cmake_install(source, install, {
        "CMAKE_OSX_ARCHITECTURES": "arm64", "CMAKE_OSX_SYSROOT": "macosx",
        "BUILD_TESTS": "OFF", "BUILD_LIBWASM": "OFF",
        "CMAKE_DISABLE_FIND_PACKAGE_Git": "TRUE",
    }, env)
    if output([wasm2c, "--version"]) != WABT_VERSION:
        raise BuildError("WABT installed with an unexpected wasm2c version.")
    return install


def verify_wasi_simde(wasi: Path, simde: Path, env: dict):
    print("Checking the WASI compiler and SIMDe headers", flush=True)
    sysroot = wasi / "share/wasi-sysroot"
    with tempfile.TemporaryDirectory(prefix="ps2-wasi-simd-") as directory:
        stage = Path(directory)
        sample = stage / "simd-probe.c"
        sample.write_text(
            '#include <simde/x86/sse2.h>\n'
            'simde__m128i add(simde__m128i a, simde__m128i b) { return simde_mm_add_epi32(a, b); }\n'
        )
        run([wasi / "bin/clang", "--target=wasm32-wasip1", f"--sysroot={sysroot}",
             "-isystem", sysroot / "include/wasm32-wasip1", "-msimd128", "-I", simde,
             "-c", sample, "-o", stage / "simd-probe.o"], env=env)
    print("OK WASI · SIMDe headers compile for the portable game core", flush=True)


def sdl_ios(source: Path, cache: Path, sdk: str, env: dict) -> tuple[Path, Path, list[str]]:
    sys.path.insert(0, str(source / "port/tools"))
    import sdl3

    xcode = output(["/usr/bin/xcodebuild", "-version"])
    key = hashlib.sha256(f"{sdk}|{xcode}|{MIN_IOS}|{sdl3.VERSION}".encode()).hexdigest()[:12]
    prefix = cache / f"SDL3-{sdl3.VERSION}-ios-{key}"
    library = prefix / "lib/libSDL3.a"
    include = prefix / "include"
    config = prefix / "lib/pkgconfig/sdl3.pc"
    if not (library.is_file() and config.is_file() and (include / "SDL3/SDL_main.h").is_file()):
        tarball = sdl3.fetch()  # The upstream helper checks SDL's pinned SHA-256.
        with tempfile.TemporaryDirectory(prefix="sdl-source-", dir=cache) as temporary:
            with tarfile.open(tarball) as tar:
                tar.extractall(temporary, filter="data")
            options = {k: v for k, v in sdl3.OPTIONS.items()
                       if k not in ("SDL_INSTALL", "SDL_DISABLE_INSTALL", "SDL_DISABLE_UNINSTALL",
                                    "HAVE_DINPUT_H", "HAVE_GAMEINPUT_H")}
            options.update(SDL_INSTALL="ON", SDL_METAL="ON", CMAKE_SYSTEM_NAME="iOS", CMAKE_OSX_SYSROOT=sdk,
                           CMAKE_OSX_ARCHITECTURES="arm64", CMAKE_OSX_DEPLOYMENT_TARGET=MIN_IOS)
            cmake_install(Path(temporary) / f"SDL3-{sdl3.VERSION}", prefix, options, env)
    flags = sdl_link_flags(config)
    if not flags:
        raise BuildError(f"SDL3 has no system link options in Libs or Libs.private in {config}.")
    return library, include, flags


def stage_touch_overlay(source: Path):
    shared = source / "port/ios/shared"
    shared.mkdir(exist_ok=True)
    for name in ("PS2AppleUI.h", "PS2AnalogGeometry.h", "PS2Touch.m",
                 "PS2DiscPicker.h", "PS2DiscPicker.m", "PS2LauncherHome.h"):
        shutil.copy2(SHARED_IOS / name, shared / name)


def verify_sdl_ios(source: Path, sdk: str, library: Path, include: Path, flags: list[str], env: dict):
    print("Checking the Downhill launcher, touch overlay and SDL3 linker options", flush=True)
    with tempfile.TemporaryDirectory(prefix="ps2-sdl-link-") as directory:
        stage = Path(directory)
        executable = stage / "SDLLinkCheck"
        stubs = stage / "link-stubs.m"
        stubs.write_text(
            '#include "dhd_settings.h"\n'
            '#include "hle/hle.h"\n'
            '#include "host/host_api.h"\n'
            '#include "ios/shared/PS2AppleUI.h"\n'
            'int dhd_launcher_main(int argc, char **argv) {\n'
            '    HostPad pad = {0};\n'
            '    SDL_Window *window = SDL_GetWindowFromID(1);\n'
            '    if (window) PS2AttachControls(window);\n'
            '    host_service_touch_field(0);\n'
            '    touch_host_merge(&pad);\n'
            '    return pad.buttons;\n'
            '}\n'
            'const char *host_disc_check(const char *path) { return 0; }\n'
            'int dhd_settings_save(const char *path, const char *key, const char *value) { return 1; }\n'
            'int dhd_settings_read(const char *path, const char *key, char *out, size_t size) { return 0; }\n'
        )
        run(["/usr/bin/xcrun", "--sdk", "iphoneos", "clang", "-target", f"arm64-apple-ios{MIN_IOS}",
             "-isysroot", sdk, "-fobjc-arc", "-fblocks", "-DDHD_IOS=1", "-I", include,
             "-I", source / "port", "-I", source / "port/render/include",
             source / "port/ios/main.m", source / "port/ios/launcher.m", source / "port/ios/touch_bridge.c",
             source / "port/ios/shared/PS2Touch.m", source / "port/ios/shared/PS2DiscPicker.m", stubs, library, *flags,
             "-framework", "Foundation", "-framework", "UIKit", "-framework", "QuartzCore",
             "-framework", "CoreGraphics", "-framework", "UniformTypeIdentifiers",
             "-Wl,-ObjC", "-Wl,-dead_strip", "-o", executable], env=env)
        with executable.open("rb") as binary:
            check_macho(binary)
        binary = executable.read_bytes()
        if DOWNHILL_UI not in binary or b"PS2PadView" not in binary:
            raise BuildError("The Downhill launcher and shared touch overlay must both link.")
    print("OK SDL3 · Downhill launcher and touch overlay linked for iPhoneOS ARM64", flush=True)


def python_venv(source: Path, cache: Path) -> Path:
    environment = cache / "venv"
    interpreter = environment / "bin/python3"
    ready = environment / ".ready"
    stamp = hashlib.sha256((source / "requirements.txt").read_bytes()).hexdigest()
    if interpreter.is_file() and ready.is_file() and ready.read_text() == stamp:
        result = capture([interpreter, "-c", "import splat, spimdisasm, rabbitizer, yaml, intervaltree"])
        if result.returncode == 0:
            return interpreter
    with tempfile.TemporaryDirectory(prefix="python-", dir=cache) as temporary:
        candidate = Path(temporary) / "venv"
        run([sys.executable, "-m", "venv", candidate])
        run([candidate / "bin/python3", "-m", "pip", "install", "--disable-pip-version-check",
             "-r", source / "requirements.txt"])
        (candidate / ".ready").write_text(stamp)
        if environment.exists():
            shutil.rmtree(environment)
        candidate.rename(environment)
    return interpreter


def rust_env(source: Path, env: dict, install: bool) -> dict:
    rustc = capture([RUSTUP, "which", "--toolchain", RUST_VERSION, "rustc"])
    rustlib = Path(rustc.stdout.strip()).parent.parent / f"lib/rustlib/{RUST_TARGET}/lib"
    if rustc.returncode or not rustlib.is_dir():
        if not install:
            raise BuildError("The pinned Rust iOS toolchain is missing. Run Install Everything.")
        run([RUSTUP, "toolchain", "install", RUST_VERSION, "--profile", "minimal", "--target", RUST_TARGET])
    rust_bin = Path(output([RUSTUP, "which", "--toolchain", RUST_VERSION, "cargo"])).parent
    result = dict(env, PATH=str(rust_bin) + os.pathsep + env["PATH"], RUSTUP_TOOLCHAIN=RUST_VERSION)
    if install:
        cwd = source / "port/render"
        fetch = [rust_bin / "cargo", "fetch", "--locked", "--target", RUST_TARGET]
        offline = subprocess.run([*fetch, "--offline"], cwd=cwd, env=result, capture_output=True)
        if offline.returncode:
            run(fetch, cwd=cwd, env=dict(result, CARGO_NET_OFFLINE="false"))
    return result


def app_bundle(source: Path, executable: Path, app: Path, profile: dict):
    app.mkdir()
    name = app.stem
    target = app / name
    shutil.copy2(executable, target)
    target.chmod(0o755)
    orientations = ["UIInterfaceOrientationLandscapeLeft", "UIInterfaceOrientationLandscapeRight"]
    info = {
        **IOS_GAMEPAD_INFO,
        "CFBundleDisplayName": "Downhill Revived",
        "CFBundleName": name,
        "CFBundleExecutable": name,
        "CFBundleIdentifier": profile["bundleID"],
        "CFBundlePackageType": "APPL",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleShortVersionString": RELEASE_VERSION,
        "CFBundleVersion": "71449",
        "CFBundleSupportedPlatforms": ["iPhoneOS"],
        "MinimumOSVersion": MIN_IOS,
        "LSRequiresIPhoneOS": True,
        "UIDeviceFamily": [1, 2],
        "UIRequiredDeviceCapabilities": ["arm64", "metal"],
        "UIFileSharingEnabled": True,
        "LSSupportsOpeningDocumentsInPlace": True,
        "UILaunchScreen": {},
        "UISupportedInterfaceOrientations": orientations,
        "UISupportedInterfaceOrientations~ipad": orientations,
        "UIRequiresFullScreen": True,
        "UIApplicationSceneManifest": {
            "UIApplicationSupportsMultipleScenes": False,
            "UISceneConfigurations": {
                "UIWindowSceneSessionRoleApplication": [{
                    "UISceneConfigurationName": "SDLSceneConfiguration",
                    "UISceneDelegateClassName": "SDLUIKitSceneDelegate",
                }]
            },
        },
    }
    with (app / "Info.plist").open("wb") as stream:
        plistlib.dump(info, stream)
    shutil.copy2(source / "port/res/icon.png", app / "GameIcon.png")
    shutil.copy2(source / "LICENSE", app / "LICENSE.txt")
    shutil.copy2(source / "port/release/THIRD-PARTY-NOTICES.txt", app / "THIRD-PARTY-NOTICES.txt")
    (app / "PortSource.txt").write_text(
        f"https://github.com/{profile['upstream']}/tree/{profile['revision']}\n"
        "The PS2Apple source includes the iOS patch and build recipe.\n"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "build"))
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--iso", type=Path)
    parser.add_argument("--elf", type=Path)
    parser.add_argument("--app", type=Path)
    args = parser.parse_args()
    if sys.platform != "darwin" or os.uname().machine != "arm64":
        parser.error("The Downhill iOS port must be built on Apple Silicon macOS with Xcode.")
    if args.action == "build" and (not args.iso or not args.elf or not args.app):
        parser.error("build requires --iso, --elf and --app")
    profile = json.loads(args.profile.read_text())
    cache = args.cache.resolve()
    cache.mkdir(parents=True, exist_ok=True)
    patch_id = hashlib.sha256(b"".join(patch.read_bytes() for patch in PORT_PATCHES)).hexdigest()[:12]
    source = cache / f"downhill-classic-{patch_id}"
    prepare_port_source(source, profile)
    stage_touch_overlay(source)
    sdk = output(["/usr/bin/xcrun", "--sdk", "iphoneos", "--show-sdk-path"])
    tools = cache / "Toolchains"
    tools.mkdir(exist_ok=True)
    env = dict(os.environ, DHD_DEVCACHES=str(tools), DHD_CARGO_DIR=str(cache / "Cargo"),
               DHD_OBJCACHE_DIR=str(cache / "Objects"), DHD_BUILD_SLOTS="1",
               PYTHONUNBUFFERED="1", DHD_SDL_JOBS=str(JOBS))
    env.pop("SDKROOT", None)
    sys.path.insert(0, str(source / "port/tools"))
    os.environ["DHD_DEVCACHES"] = str(tools)
    if args.action == "prepare":
        print("Preparing Python, WASI SDK, WABT, SIMDe, Rust and static SDL3.", flush=True)
        python_venv(source, cache)
        wasi = wasi_sdk(tools)
        wabt_tool(tools, env)
        simde = tools / "simde"
        checkout(simde, "https://github.com/simd-everywhere/simde.git", SIMDE_REV)
        apply_tool_patch(simde, SIMDE_WASI_PATCH)
        verify_wasi_simde(wasi, simde, env)
        rust_env(source, env, install=True)
        library, include, flags = sdl_ios(source, tools, sdk, env)
        verify_sdl_ios(source, sdk, library, include, flags, env)
        return

    if not args.elf.is_file():
        raise BuildError("The verified PS2 boot ELF is missing.")
    with args.elf.open("rb") as stream:
        if hashlib.file_digest(stream, "sha1").hexdigest() != profile["bootSHA1"]:
            raise BuildError("The extracted ELF changed after validation.")
    python = cache / "venv/bin/python3"
    wasi = tools / WASI_NAME
    wabt = tools / f"wabt-{WABT_VERSION}"
    extracted = source / "iso/SCUS_971.77"
    marker = source / "build/.ps2-split-ready"
    if not (marker.is_file() and marker.read_text() == profile["bootSHA1"] and
            (source / "assets/vutext.bin").is_file() and (source / "asm").is_dir()):
        progress(.36, "Splitting the verified retail ELF")
        marker.unlink(missing_ok=True)
        extracted.parent.mkdir(exist_ok=True)
        shutil.copy2(args.elf, extracted)
        run([python, source / "tools/split.py"], cwd=source, env=env)
        marker.parent.mkdir(exist_ok=True)
        marker.write_text(profile["bootSHA1"])
    core_env = dict(env, PATH=os.pathsep.join([str(wasi / "bin"), str(wabt / "bin"), env["PATH"]]),
                    WASI_SYSROOT=str(wasi / "share/wasi-sysroot"), SIMDE_DIR=str(tools / "simde"),
                    WABT_DIR=str(wabt), DHD_DATA_CLANG=output(["/usr/bin/xcrun", "--sdk", "macosx", "--find", "clang"]))
    progress(.45, "Compiling and linking the portable game core")
    run([python, source / "port/tools/build_wasm.py", "--fail-fast", "-j", str(JOBS)], cwd=source, env=core_env)
    progress(.69, "Compiling the static SDL, Metal renderer and game host for iPhoneOS")
    host_env = rust_env(source, env, install=False)
    host_env.update(SDKROOT=sdk, IPHONEOS_DEPLOYMENT_TARGET=MIN_IOS, SIMDE_DIR=str(tools / "simde"),
                    WABT_DIR=str(wabt))
    sdl_lib, sdl_include, flags = sdl_ios(source, tools, sdk, env)
    native = source / "build/host64-ios"
    run([python, source / "port/tools/build_host64.py", "--target", "ios-arm64", "--release",
         "--jobs", str(JOBS), "--sdl-lib", sdl_lib, "--sdl-include", sdl_include, "--sdl-libs", shlex.join(flags),
         "--out", native, source / "build/wasm/dhd_core.wasm"], cwd=source, env=host_env)
    progress(.87, "Staging the unsigned iPhoneOS app bundle")
    app_bundle(source, native / "DownhillRevived", args.app, profile)


if __name__ == "__main__":
    try:
        main()
    except (BuildError, OSError, ValueError, tarfile.TarError) as error:
        print(f"ERROR: {error}", file=sys.stderr, flush=True)
        sys.exit(1)
