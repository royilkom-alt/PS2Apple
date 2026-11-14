"""Shared parsing for iPhoneOS SDL link metadata."""
import re
import shlex
import shutil
import tempfile
from pathlib import Path


def sdl_link_flags(config: Path) -> list[str]:
    fields = dict(re.findall(r"^([\w.]+)[ \t]*[:=][ \t]*(.*)$", config.read_text(), re.M))

    def expand(value):
        return re.sub(r"\$\{(\w+)\}", lambda match: expand(fields.get(match[1], "")), value)

    # SDL puts its system dependencies in Libs when it only builds a static archive.
    tokens = iter(shlex.split(fields.get("Libs", "")) + shlex.split(fields.get("Libs.private", "")))
    flags = []
    for token in tokens:
        if token == "-L":
            next(tokens, None)
        elif not token.startswith("-L") and token not in ("-lSDL3", "-lSDL3-static"):
            flags.append(expand(token))
    return flags


SDL_REV = "829a65d769d935c4852f8159e964312c0957260a"
IOS_GAMEPAD_INFO = {
    "GCSupportsControllerUserInteraction": True,
    "GCSupportedGameControllers": [{"ProfileName": "ExtendedGamepad"}],
    "NSBluetoothAlwaysUsageDescription": "Find wireless gamepads so you can play with a controller.",
}


def xcode_cmake(sdk: str):
    from builder import MIN_IOS, TOOLS

    return ["-G", "Ninja", f"-DCMAKE_MAKE_PROGRAM={TOOLS['ninja']}", "-DCMAKE_BUILD_TYPE=Release",
            "-DCMAKE_SYSTEM_NAME=iOS", f"-DCMAKE_OSX_SYSROOT={sdk}", "-DCMAKE_OSX_ARCHITECTURES=arm64",
            f"-DCMAKE_OSX_DEPLOYMENT_TARGET={MIN_IOS}", "-DCMAKE_TRY_COMPILE_TARGET_TYPE=STATIC_LIBRARY"]


def prepare_sdl(tools: Path, sdk: str, key: str, jobs: int):
    from builder import BuildError, TOOLS, checkout, run

    target = tools / f"SDL3-3.4.18-Metal-{key}"
    library = target / "lib/libSDL3.a"
    if library.is_file() and (target / "lib/pkgconfig/sdl3.pc").is_file() and (target / "include/SDL3/SDL.h").is_file():
        return target
    source = tools / "SDL3-3.4.18-source"
    checkout(source, "https://github.com/libsdl-org/SDL.git", SDL_REV)
    print("Building SDL3 for iPhoneOS with the Metal renderer.", flush=True)
    with tempfile.TemporaryDirectory(prefix="sdl-metal-", dir=tools) as temporary:
        stage = Path(temporary)
        run([TOOLS["cmake"], "-S", source, "-B", stage / "build", *xcode_cmake(sdk),
             f"-DCMAKE_INSTALL_PREFIX={stage / 'install'}", "-DCMAKE_INSTALL_LIBDIR=lib",
             "-DSDL_SHARED=OFF", "-DSDL_STATIC=ON", "-DSDL_TEST_LIBRARY=OFF", "-DSDL_TESTS=OFF",
             "-DSDL_EXAMPLES=OFF", "-DSDL_INSTALL=ON", "-DSDL_RENDER=ON", "-DSDL_METAL=ON",
             "-DSDL_OPENGL=OFF", "-DSDL_OPENGLES=OFF", "-DSDL_VULKAN=OFF", "-DSDL_GPU=OFF"])
        run([TOOLS["cmake"], "--build", stage / "build", "--parallel", str(jobs)])
        run([TOOLS["cmake"], "--install", stage / "build"])
        if not (stage / "install/lib/libSDL3.a").is_file():
            raise BuildError("SDL3 did not produce an iPhoneOS static archive.")
        if target.exists():
            shutil.rmtree(target)
        (stage / "install").rename(target)
    return target
