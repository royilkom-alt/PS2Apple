#!/bin/bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
    printf '%s\n' 'ERROR: SwiftUI .app compilation requires macOS on Apple Silicon with a macOS SDK.' >&2
    exit 69
fi

destination="${1:-$root/dist}"
mkdir -p "$destination"
destination="$(cd "$destination" && pwd)"
name='PS2Apple.app'
stage="$(mktemp -d "$destination/.ps2-app.XXXXXX")"
trap 'rm -rf "$stage"' EXIT
app="$stage/$name"
macos="$app/Contents/MacOS"
resources="$app/Contents/Resources"
mkdir -p "$macos" "$resources/Backend"

sdk="$(/usr/bin/xcrun --sdk macosx --show-sdk-path)"
swift="$(/usr/bin/xcrun --sdk macosx --find swiftc)"
executable="$macos/PS2Apple"
printf '%s\n' "Compiling the SwiftUI app with $sdk"
"$swift" -parse-as-library -swift-version 5 -O -target arm64-apple-macosx13.0 -sdk "$sdk" \
    "$root"/Sources/*.swift -framework SwiftUI -framework AppKit -o "$executable"

cp "$root/Resources/Info.plist" "$app/Contents/Info.plist"
version="$(tr -d '[:space:]' < "$root/Backend/VERSION")"
/usr/bin/plutil -replace CFBundleShortVersionString -string "$version" "$app/Contents/Info.plist"
/usr/bin/plutil -lint "$app/Contents/Info.plist"
# Copy only the runtime sources and the bundled profiles, even from a development checkout.
for backend in "$root"/Backend/*.py; do
    case "${backend##*/}" in
        windows_bridge.py|windows_build.py) ;;
        *) cp "$backend" "$resources/Backend/" ;;
    esac
done
cp "$root"/Backend/*.sh "$root"/Backend/*.command "$root/Backend/VERSION" "$resources/Backend/"
cp "$root/Backend/game-catalog.json" "$resources/Backend/"
cp -R "$root/Backend/ios" "$resources/Backend/"
cp -R "$root/Backend/engine" "$resources/Backend/"
mkdir "$resources/Backend/ports"
for profile in "$root"/Backend/ports/*; do
    [[ -d "$profile" ]] || continue
    port="$resources/Backend/ports/${profile##*/}"
    mkdir "$port"
    for source in "$profile/profile.json" "$profile"/*.py "$profile"/*.patch; do
        if [[ -f "$source" ]]; then
            cp "$source" "$port/"
        fi
    done
    for runtime in ios engine maps; do
        if [[ -d "$profile/$runtime" ]]; then
            cp -R "$profile/$runtime" "$port/"
        fi
    done
done
find "$resources/Backend" -type d -name __pycache__ -prune -exec rm -rf {} +
cp "$root/Resources/YouTube.png" "$root/Resources/Discord.png" "$resources/"
chmod +x "$resources/Backend/"*.sh "$resources/Backend/"*.command

icons="$stage/AppIcon.iconset"
mkdir "$icons"
for size in 16 32 128 256 512; do
    /usr/bin/sips -z "$size" "$size" "$root/Resources/AppIcon.png" \
        --out "$icons/icon_${size}x${size}.png" >/dev/null
    double=$((size * 2))
    /usr/bin/sips -z "$double" "$double" "$root/Resources/AppIcon.png" \
        --out "$icons/icon_${size}x${size}@2x.png" >/dev/null
done
/usr/bin/iconutil -c icns -o "$resources/AppIcon.icns" "$icons"
/usr/bin/codesign --force --sign - --timestamp=none "$app"
/usr/bin/codesign --verify --strict "$app"
[[ "$(/usr/bin/xcrun lipo -archs "$executable")" == "arm64" ]]
/usr/bin/xcrun vtool -show-build "$executable"

installed="$destination/$name"
existing="$installed"
if [[ ! -e "$existing" && ! -L "$existing" ]]; then
    existing="$destination/PS2 Native Easy Builder.app"
fi
if [[ -e "$existing" || -L "$existing" ]]; then
    backups="$HOME/Library/Application Support/PS2 Native Easy Builder/App Backups"
    mkdir -p "$backups"
    backup="$(mktemp -d "$backups/Previous.XXXXXX")"
    mv "$existing" "$backup/"
    saved="$backup/${existing##*/}"
    printf '%s\n' "Previous application saved at $saved"
    if ! mv "$app" "$installed"; then
        mv "$saved" "$existing"
        exit 1
    fi
else
    mv "$app" "$installed"
fi
printf '%s\n' "Built $installed"
