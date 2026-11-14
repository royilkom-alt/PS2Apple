import hashlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


recipe_path = Path(__file__).resolve().parents[1] / "Backend/ports/downhill/build.py"
spec = importlib.util.spec_from_file_location("downhill_recipe", recipe_path)
recipe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recipe)


class SDLRecipeTests(unittest.TestCase):
    def test_reuses_static_only_cache_with_frameworks_in_libs(self):
        sdk = "/Applications/Xcode.app/Contents/Developer/Platforms/iPhoneOS.platform/Developer/SDKs/iPhoneOS.sdk"
        xcode = "Xcode 27.2\nBuild version 27B52"
        sdl = SimpleNamespace(VERSION="3.4.18")
        key = hashlib.sha256(f"{sdk}|{xcode}|{recipe.MIN_IOS}|{sdl.VERSION}".encode()).hexdigest()[:12]
        with tempfile.TemporaryDirectory(prefix="PS2 Builder Cache ") as directory:
            cache = Path(directory)
            prefix = cache / f"SDL3-{sdl.VERSION}-ios-{key}"
            library = prefix / "lib/libSDL3.a"
            config = prefix / "lib/pkgconfig/sdl3.pc"
            header = prefix / "include/SDL3/SDL_main.h"
            config.parent.mkdir(parents=True)
            header.parent.mkdir(parents=True)
            library.write_bytes(b"cached SDL archive")
            header.touch()
            config.write_text(
                "prefix=${pcfiledir}/../..\nlibdir=${prefix}/lib\n"
                "Libs: -L${libdir} -lSDL3 -Wl,-framework,UIKit -Wl,-framework,Metal\n"
                "Cflags: -I${prefix}/include\n"
            )
            with patch.dict(sys.modules, {"sdl3": sdl}), patch.object(recipe, "output", return_value=xcode), \
                    patch.object(recipe, "cmake_install") as install:
                archive, include, flags = recipe.sdl_ios(cache, cache, sdk, {})
            install.assert_not_called()
            self.assertEqual(archive, library)
            self.assertEqual(include, prefix / "include")
            self.assertEqual(flags, ["-Wl,-framework,UIKit", "-Wl,-framework,Metal"])

    def test_reads_public_and_private_dependencies_in_order(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "sdl3.pc"
            config.write_text(
                'sdk_framework=UIKit\n'
                'Libs: -L "SDL install path" -lSDL3 -framework ${sdk_framework}\n'
                'Libs.private: -Wl,-weak_framework,CoreHaptics -lSDL3-static -lm\n'
            )
            self.assertEqual(recipe.sdl_link_flags(config),
                             ["-framework", "UIKit", "-Wl,-weak_framework,CoreHaptics", "-lm"])

    def test_empty_private_line_does_not_read_compiler_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "sdl3.pc"
            config.write_text("Libs: -lSDL3\nLibs.private: \nCflags: -I${includedir}\n")
            self.assertEqual(recipe.sdl_link_flags(config), [])
