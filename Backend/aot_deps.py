"""Pinned shader runtime and source used by the iPhoneOS GS backends."""
from __future__ import annotations

import hashlib
import shutil
import tempfile
import zipfile
from pathlib import Path

from builder import BuildError, run
from engine_source import ENGINE_ROOT

WGPU_VERSION = "v29.0.1.1"
WGPU_SHA256 = "e36c9913b9e5095a530fa9121c50b16a4e3dd020e1eebf601f2f47ce24d56941"
WGPU_FILE = "wgpu-ios-aarch64-release.zip"
MAC_WGPU_SHA256 = "a5797a37b1adf720bcd5dcffb291edbbd5b7b14be0a3874c28e6393a655a7a3e"
MAC_WGPU_FILE = "wgpu-macos-aarch64-release.zip"
WGPU_FILES = ("lib/libwgpu_native.a", "include/webgpu/webgpu.h", "include/webgpu/wgpu.h")


def engine_source() -> Path:
    required = ("CMakeLists.txt", "recomp/main.cpp", "runtime/src/system.cpp", "LICENSE")
    if not all((ENGINE_ROOT / name).is_file() for name in required):
        raise BuildError("The bundled PS2Apple compiler is incomplete. Reinstall PS2Apple from the source package.")
    return ENGINE_ROOT


def prepare_wgpu(tools: Path, *, mac: bool = False) -> Path:
    platform = "macos" if mac else "iphoneos"
    archive_name = MAC_WGPU_FILE if mac else WGPU_FILE
    expected_hash = MAC_WGPU_SHA256 if mac else WGPU_SHA256
    target = tools / f"wgpu-{WGPU_VERSION}-{platform}-arm64"
    if all((target / name).is_file() for name in WGPU_FILES):
        return target
    with tempfile.TemporaryDirectory(prefix="metal-shaders-", dir=tools) as temporary:
        stage = Path(temporary)
        archive = stage / WGPU_FILE
        url = f"https://github.com/gfx-rs/wgpu-native/releases/download/{WGPU_VERSION}/{archive_name}"
        run(["/usr/bin/curl", "--fail", "--silent", "--show-error", "--location", "--retry", "2",
             "--connect-timeout", "15", "--max-time", "180", "--output", archive, url])
        with archive.open("rb") as stream:
            sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
        if sha256 != expected_hash:
            raise BuildError(f"The {platform} shader runtime download did not match its pinned SHA-256.")
        install = stage / "install"
        with zipfile.ZipFile(archive) as package:
            for name in WGPU_FILES:
                path = install / name
                path.parent.mkdir(parents=True, exist_ok=True)
                with package.open(name) as source, path.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
        if target.exists():
            shutil.rmtree(target)
        install.rename(target)
    return target
