#!/bin/sh
# Install wt-status and its `wt` alias.
#
#   curl -fsSL https://raw.githubusercontent.com/RomualdP/wt-status/main/install.sh | sh
#   ./install.sh                     # from a clone
#   WT_INSTALL_DIR=~/bin ./install.sh
set -eu

REPO_RAW="https://raw.githubusercontent.com/RomualdP/wt-status/main"
DEST="${WT_INSTALL_DIR:-$HOME/.local/bin}"

err() { printf 'error: %s\n' "$1" >&2; exit 1; }

command -v git >/dev/null 2>&1 || err "git is required"
command -v python3 >/dev/null 2>&1 || err "python3 (>= 3.9) is required"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' || err "python3 >= 3.9 is required"

mkdir -p "$DEST"
here="$(cd "$(dirname "$0")" 2>/dev/null && pwd || true)"
if [ -n "$here" ] && [ -f "$here/wt-status" ]; then
    cp "$here/wt-status" "$DEST/wt-status"
else
    command -v curl >/dev/null 2>&1 || err "curl is required"
    tmp="$(mktemp)"
    curl -fsSL "$REPO_RAW/wt-status" -o "$tmp" || err "download failed"
    head -n 1 "$tmp" | grep -q python3 || { rm -f "$tmp"; err "unexpected download content"; }
    mv "$tmp" "$DEST/wt-status"
fi
chmod +x "$DEST/wt-status"

if [ -e "$DEST/wt" ] && [ ! -L "$DEST/wt" ]; then
    printf 'note: %s/wt already exists and is not ours; left untouched (use `wt-status -i`).\n' "$DEST"
else
    ln -sf wt-status "$DEST/wt"
fi

printf 'Installed %s/wt-status and %s/wt\n' "$DEST" "$DEST"
case ":$PATH:" in
    *":$DEST:"*) ;;
    *) printf '\n%s is not in your PATH. Add this to your shell profile:\n  export PATH="%s:$PATH"\n' "$DEST" "$DEST" ;;
esac
