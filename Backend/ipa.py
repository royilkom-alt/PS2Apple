"""Validate and export an unsigned device IPA from a staged native app."""
from __future__ import annotations

import json
import mmap
import os
import plistlib
import shutil
import stat
import struct
import subprocess
import tempfile
import zipfile
from pathlib import Path

IOS = 2
ARM64 = 0x0100000C
MH_OBJECT = 1
MH_EXECUTE = 2
SHARED_UI = b"PS2AppleSharedUI-v1\0"
DOWNHILL_UI = b"PS2AppleDownhillUI-v1\0"
FRONTENDS = {"shared": SHARED_UI, "downhill": DOWNHILL_UI}


def stage_ps2apple_icon(app: Path, min_ios: str, *, platform: str = "iphoneos") -> None:
    if app.is_symlink() or any(path.is_symlink() for path in app.rglob("*")):
        raise ValueError("The staged iOS app must not contain symbolic links.")
    info_path = app / "Info.plist"
    with info_path.open("rb") as stream:
        info = plistlib.load(stream)
    icon = Path(__file__).resolve().parent / "ios/PS2AppleIcon.png"
    name = "PS2AppleAppIcon"
    old_icon_name = info.get("CFBundleIcons", {}).get("CFBundlePrimaryIcon", {}).get("CFBundleIconName")
    existing_catalog = (app / "Assets.car").exists() and old_icon_name != name
    with tempfile.TemporaryDirectory(prefix="ps2apple-icon-", dir=app.parent) as temporary:
        stage = Path(temporary)
        assets = stage / "Brand.xcassets"
        icon_set = assets / f"{name}.appiconset"
        icon_set.mkdir(parents=True)
        shutil.copyfile(icon, icon_set / "Icon.png")
        (icon_set / "Contents.json").write_text(json.dumps({
            "images": [{"filename": "Icon.png", "idiom": "universal", "platform": "ios", "size": "1024x1024"}],
            "info": {"author": "xcode", "version": 1},
        }), encoding="utf-8")
        compiled = stage / "compiled"
        compiled.mkdir()
        partial = stage / "Icon.plist"
        result = subprocess.run(["/usr/bin/xcrun", "--sdk", platform, "actool", "--compile", str(compiled),
                                 "--platform", platform, "--minimum-deployment-target", min_ios,
                                 "--target-device", "iphone", "--target-device", "ipad", "--app-icon", name,
                                 "--output-partial-info-plist", str(partial), str(assets)],
                                capture_output=True, text=True)
        if result.returncode:
            raise ValueError(f"Xcode could not compile the PS2Apple icon.\n{result.stdout}{result.stderr}")
        for file in compiled.iterdir():
            if not existing_catalog or file.name != "Assets.car":
                shutil.copyfile(file, app / file.name)
        shutil.copyfile(icon, app / icon.name)
        with partial.open("rb") as stream:
            metadata = plistlib.load(stream)
        for key in ("CFBundleIcons", "CFBundleIcons~ipad"):
            primary = metadata[key]["CFBundlePrimaryIcon"]
            if existing_catalog:
                # Keep a custom port's asset catalog and use the standalone primary icons.
                primary.pop("CFBundleIconName", None)
            info.setdefault(key, {})["CFBundlePrimaryIcon"] = primary
    with info_path.open("wb") as stream:
        plistlib.dump(info, stream)


def check_macho(stream, kind: int = MH_EXECUTE) -> None:
    head = stream.read(32)
    if len(head) != 32:
        raise ValueError("The Mach-O header is incomplete.")
    magic, cpu, _, filetype, count, size, _, _ = struct.unpack("<8I", head)
    if (magic, cpu, filetype) != (0xFEEDFACF, ARM64, kind):
        raise ValueError("Expected a thin ARM64 Mach-O of the requested type.")
    if size > 1024 * 1024 or count > size // 8:
        raise ValueError("The Mach-O load command table is invalid.")
    commands = stream.read(size)
    if len(commands) != size:
        raise ValueError("The Mach-O load command table is incomplete.")
    offset = 0
    platforms = []
    for _ in range(count):
        if offset + 8 > size:
            raise ValueError("A Mach-O load command exceeds its table.")
        command, length = struct.unpack_from("<II", commands, offset)
        if length < 8 or length % 8 or offset + length > size:
            raise ValueError("A Mach-O load command has an invalid size.")
        if command == 0x32:
            if length < 24:
                raise ValueError("The Mach-O build version is incomplete.")
            platforms.append(struct.unpack_from("<I", commands, offset + 8)[0])
        elif command == 0x25:
            if length != 16:
                raise ValueError("The Mach-O iPhoneOS version command is invalid.")
            platforms.append(IOS)
        offset += length
    if offset != size or platforms != [IOS]:
        raise ValueError("Expected an iPhoneOS device build. macOS and simulator binaries cannot be exported.")


def verify_app(app: Path, bundle_id: str, *, frontend: str = "shared") -> str:
    marker = FRONTENDS[frontend]
    with (app / "Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    name = info.get("CFBundleExecutable")
    if not isinstance(name, str) or not name or Path(name).name != name or name in (".", ".."):
        raise ValueError("Info.plist does not name an executable in the app bundle.")
    if (info.get("CFBundleIdentifier") != bundle_id
            or info.get("CFBundleSupportedPlatforms") != ["iPhoneOS"]
            or info.get("CFBundlePackageType") != "APPL"):
        raise ValueError("The app bundle metadata does not match this iPhoneOS port profile.")
    executable = app / name
    if os.name != "nt" and not stat.S_IMODE(executable.stat().st_mode) & 0o111:
        raise ValueError("The app's executable has no execute permission.")
    with executable.open("rb") as stream:
        check_macho(stream)
        with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as code:
            if code.find(marker) < 0:
                raise ValueError(f"The game must link its {frontend} launcher and controls before IPA export.")
    return name


def export_ipa(app: Path, target: Path, bundle_id: str, *, frontend: str = "shared") -> None:
    if app.suffix != ".app" or app.is_symlink():
        raise ValueError("The port did not produce a staged .app directory.")
    executable = verify_app(app, bundle_id, frontend=frontend)
    prefix = f"Payload/{app.name}/"
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".ps2-build-", suffix=".partial", dir=target.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=5) as archive:
            for path in sorted(app.rglob("*")):
                if path.is_symlink():
                    raise ValueError(f"Unexpected symbolic link in the app bundle: {path.name}")
                if path.is_file():
                    if path.suffix.lower() in (".iso", ".chd", ".bios"):
                        raise ValueError(f"Unexpected disc or BIOS file in the app bundle: {path.name}")
                    name = prefix + path.relative_to(app).as_posix()
                    if os.name == "nt":
                        # Windows file attributes have no Unix execute bit.
                        # Preserve it explicitly for the app's Mach-O in the IPA.
                        entry = zipfile.ZipInfo.from_file(path, name)
                        entry.create_system = 3
                        entry.external_attr = (stat.S_IFREG | (0o755 if name == prefix + executable else 0o644)) << 16
                        entry.compress_type = zipfile.ZIP_DEFLATED
                        with path.open("rb") as source, archive.open(entry, "w") as destination:
                            shutil.copyfileobj(source, destination)
                    else:
                        archive.write(path, name)
        with zipfile.ZipFile(temporary) as archive:
            damaged = archive.testzip()
            if damaged:
                raise ValueError(f"Corrupt entry in the IPA: {damaged}")
            mode = archive.getinfo(prefix + executable).external_attr >> 16
            if not mode & 0o111:
                raise ValueError("The IPA lost the app's execute permissions.")
            with archive.open(prefix + executable) as stream:
                check_macho(stream)
        os.chmod(temporary, 0o644)
        if os.name == "nt":
            os.rename(temporary, target)  # Windows rename fails if the destination already exists.
        else:
            os.link(temporary, target)  # Publish without overwriting another build.
    finally:
        Path(temporary).unlink(missing_ok=True)
