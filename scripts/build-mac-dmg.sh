#!/bin/bash
set -euo pipefail
if [[ $# != 2 ]]; then
    printf '%s\n' "Usage: $0 <PS2Apple.app> <output.dmg>" >&2
    exit 64
fi
app="$1"
destination="$2"
version="$(/usr/bin/plutil -extract CFBundleShortVersionString raw "$app/Contents/Info.plist")"
if [[ "$version" == "1.0.0" ]]; then
    version="1.0"
fi
/usr/bin/codesign --verify --strict "$app"
stage="$(mktemp -d "${TMPDIR:-/tmp}/ps2apple-dmg.XXXXXX")"
trap 'rm -rf "$stage"' EXIT
/usr/bin/ditto "$app" "$stage/PS2Apple.app"
ln -s /Applications "$stage/Applications"
mkdir -p "$(dirname "$destination")"
/usr/bin/hdiutil create -format UDZO -fs HFS+ -volname "PS2Apple $version" -srcfolder "$stage" "$destination"
/usr/bin/hdiutil verify "$destination"
