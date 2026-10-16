#!/bin/bash
# Install mp-data, mp-run and mp-bash-hook from this checkout into $HOME/.local/bin as mode-755
# copies (never symlinks, so edits in a worktree never change the installed tools) and create
# $HOME/.local/share/modernization/. Idempotent. Run by the mp_data environment item and mp-data pin.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
bin="$HOME/.local/bin"
mkdir -p "$bin" "$HOME/.local/share/modernization"
for f in mp-data mp-run mp-bash-hook; do
  tmp=$(mktemp "$bin/.$f.XXXXXX")
  cp "$here/$f" "$tmp"
  chmod 755 "$tmp"
  mv -f "$tmp" "$bin/$f"
done
echo "installed mp-data, mp-run and mp-bash-hook from $here into $bin" >&2
