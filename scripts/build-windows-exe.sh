#!/bin/bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ $# != 3 ]]; then
    printf '%s\n' "Usage: $0 <python-3.14.10-embed-amd64.zip> <windows-toolchain-kit> <output-folder>" >&2
    exit 64
fi
python_zip="$1"
kit="$2"
destination="$3"
stage="$(mktemp -d "${TMPDIR:-/tmp}/ps2apple-win.XXXXXX")"
trap 'rm -rf "$stage"' EXIT
/usr/bin/sips -z 256 256 "$root/Resources/AppIcon.png" --out "$stage/icon.png" >/dev/null
python3 - "$root" "$python_zip" "$kit" "$stage" <<'PY'
import hashlib
import struct
import sys
import zipfile
from pathlib import Path

root, archive, kit, stage = map(Path, sys.argv[1:])
expected = "90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159"
if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
    sys.exit("The Windows Python archive does not match the pinned release.")
with zipfile.ZipFile(archive) as source, zipfile.ZipFile(stage / "components.zip", "w", zipfile.ZIP_DEFLATED) as package:
    for member in source.infolist():
        contents = source.read(member)
        if member.filename.endswith("._pth"):
            contents = b"python314.zip\n.\nBackend\n"
        package.writestr(member.filename, contents)
    backend = root / "Backend"
    names = ("builder.py", "disc.py", "ipa.py", "engine_source.py", "port_search.py", "game-catalog.json",
             "VERSION", "windows_build.py", "ios_deps.py", "aot_deps.py", "ee_discovery.py",
             "ports/generic/build.py", "ports/generic/aot_maps.py", "engine/scripts/disc.py")
    maps = sorted((backend / "ports/generic/maps").glob("*.json"))
    headers = sorted((backend / "engine/runtime/include").rglob("*.hpp"))
    for path in [*(backend / name for name in names), *maps, *headers]:
        package.write(path, path.relative_to(root))
    if not all((kit / name).is_file() for name in (
        "Probe/eerecomp.exe", "Probe/vurecomp.exe", "Zig/zig.exe", "LLVM/ld64.lld.exe", "iPhoneOS/link.json"
    )):
        sys.exit("Run stage-windows-kit.py to prepare the complete Windows build environment.")
    for path in sorted(kit.rglob("*")):
        if path.is_symlink() or path.suffix.lower() in {".ipa", ".iso", ".elf", ".chd", ".bios", ".app"}:
            sys.exit(f"The Windows build kit contains an unexpected file: {path.name}")
        if path.is_file():
            package.write(path, Path("Toolchain") / path.relative_to(kit))
image = (stage / "icon.png").read_bytes()
(stage / "icon.ico").write_bytes(struct.pack("<HHHBBBBHHII", 0, 1, 1, 0, 0, 0, 0, 1, 32, len(image), 22) + image)
PY
version="$(tr -d '[:space:]' < "$root/Backend/VERSION")"
dotnet publish "$root/Windows/PS2Apple.csproj" -c Release -r win-x64 --self-contained \
    -p:Version="$version" -p:PS2AppleComponents="$stage/components.zip" -p:PS2AppleIcon="$stage/icon.ico" \
    -p:BaseIntermediateOutputPath="$stage/obj/" -p:BaseOutputPath="$stage/bin/" -o "$stage/publish"
mkdir -p "$destination"
cp "$stage/publish/PS2Apple.exe" "$destination/PS2Apple.exe"
printf '%s\n' "Built $destination/PS2Apple.exe"
