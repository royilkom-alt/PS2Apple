#!/bin/bash
# Run only when Xcode or Homebrew needs an interactive administrator prompt.
set -euo pipefail
backend="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec /bin/bash "$backend/launch.sh" setup-aot
