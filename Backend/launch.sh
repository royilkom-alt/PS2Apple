#!/bin/bash
# Resolve a local Xcode and bootstrap Homebrew/Python before the Python backend.
set -euo pipefail
action="${1:-check}"
if [[ "$action" == --workspace && $# -ge 3 ]]; then action="$3"; fi
if [[ "$action" == build ]]; then
    printf '%s\n' 'Starting the build backend'
fi
backend="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/bin:/bin:/usr/sbin:/sbin"
export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1
export HOMEBREW_NO_INSTALL_CLEANUP=1

report() {
    printf '%s\n' "ERROR: $1" "@@PS2 {\"kind\":\"error\",\"detail\":\"$1\"}"
    exit 1
}

if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
    report 'An Apple Silicon Mac running macOS is required.'
fi

developer="${DEVELOPER_DIR:-$(/usr/bin/xcode-select -p 2>/dev/null || true)}"
if [[ "$developer" == *.app ]]; then developer="$developer/Contents/Developer"; fi
if [[ -z "$developer" || ! -x "$developer/usr/bin/xcodebuild" ]]; then
    developer=""
    for candidate in /Applications/Xcode.app /Applications/Xcode*.app; do
        if [[ -x "$candidate/Contents/Developer/usr/bin/xcodebuild" ]]; then
            developer="$candidate/Contents/Developer"
            break
        fi
    done
fi
if [[ -n "$developer" ]]; then
    export DEVELOPER_DIR="$developer"
else
    unset DEVELOPER_DIR
fi

brew=/opt/homebrew/bin/brew
python=/opt/homebrew/opt/python@3.12/bin/python3.12
if [[ "$action" == setup || "$action" == setup-tools || "$action" == setup-aot ]]; then
    [[ -n "$developer" ]] || report 'Install full Xcode and open it once, then run Install Everything again.'
    if [[ ! -x "$brew" ]]; then
        installer="$(mktemp -t ps2-homebrew)"
        trap 'rm -f "$installer"' EXIT
        printf '%s\n' 'Downloading the official Homebrew installer.'
        /usr/bin/curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location \
            'https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh' -o "$installer"
        if [[ -t 0 ]]; then
            /bin/bash "$installer"
        elif ! NONINTERACTIVE=1 /bin/bash "$installer"; then
            report 'Homebrew requires an administrator or interactive approval. Use Open Setup in Terminal, then retry.'
        fi
        rm -f "$installer"
        trap - EXIT
    fi
    [[ -x "$brew" ]] || report 'Homebrew was not installed at /opt/homebrew/bin/brew.'
    if [[ ! -x "$python" ]]; then
        if "$brew" list --versions python@3.12 >/dev/null 2>&1; then
            "$brew" reinstall python@3.12
        else
            "$brew" install python@3.12
        fi
    fi
fi

if [[ ! -x "$python" ]]; then
    if [[ -n "$developer" ]] && /usr/bin/python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
        python=/usr/bin/python3
    elif [[ "$action" == check ]]; then
        printf '%s\n' '@@PS2 {"kind":"check","key":"arm64","ok":true,"detail":"Apple Silicon / ARM64"}'
        if [[ -x "$brew" ]]; then
            printf '%s\n' '@@PS2 {"kind":"check","key":"homebrew","ok":true,"detail":"/opt/homebrew/bin/brew"}'
        else
            printf '%s\n' '@@PS2 {"kind":"check","key":"homebrew","ok":false,"detail":"Apple Silicon Homebrew is required."}'
        fi
        printf '%s\n' \
            '@@PS2 {"kind":"check","key":"xcode","ok":false,"detail":"Install full Xcode and complete its first launch."}' \
            '@@PS2 {"kind":"check","key":"sdk","ok":false,"detail":"iPhoneOS SDK has not been verified."}' \
            '@@PS2 {"kind":"check","key":"compiler","ok":false,"detail":"ARM64 compilation has not been verified."}' \
            '@@PS2 {"kind":"check","key":"tools","ok":false,"detail":"Run Install Everything after opening Xcode."}'
        exit 0
    else
        report 'Python 3.12 is missing. Run Install Everything first.'
    fi
fi

exec "$python" -u "$backend/builder.py" "$@"
