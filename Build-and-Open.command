#!/bin/bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
destination="$HOME/Applications"
"$root/scripts/build-mac-app.sh" "$destination"
/usr/bin/open "$destination/PS2Apple.app"
