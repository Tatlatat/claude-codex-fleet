#!/usr/bin/env bash
# claude-reasonix uninstaller — removes what install.sh added. It does NOT touch
# claude/node (we never installed those). The engine is the bundled fork called
# in-process — there is no longer any upstream-reasonix dist patch to revert.
#
# Usage:  ./uninstall.sh [--purge]
#           --purge          also delete runtime logs/ledgers/state under INSTALL_HOME
set -euo pipefail

INSTALL_HOME="${CLAUDE_REASONIX_FLEET_INSTALL_HOME:-$HOME/.claude/reasonix-fleet}"
BIN_DIR="${CLAUDE_REASONIX_BIN_DIR:-$HOME/.local/bin}"
LAUNCHER_NAME="claude-reasonix"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PURGE=0
for arg in "$@"; do
  case "$arg" in
    --purge)        PURGE=1 ;;
    *) printf 'unknown option: %s\n' "$arg" >&2; exit 1 ;;
  esac
done

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m  !\033[0m %s\n' "$*"; }

say "Removing the launcher"
if [ -e "$BIN_DIR/$LAUNCHER_NAME" ] || [ -L "$BIN_DIR/$LAUNCHER_NAME" ]; then
  rm -f "$BIN_DIR/$LAUNCHER_NAME"
  ok "removed $BIN_DIR/$LAUNCHER_NAME"
else
  warn "no launcher at $BIN_DIR/$LAUNCHER_NAME"
fi

say "Removing the fleet"
if [ -d "$INSTALL_HOME" ]; then
  if [ "$PURGE" -eq 1 ]; then
    rm -rf "$INSTALL_HOME"
    ok "deleted $INSTALL_HOME (including runtime logs/ledgers/state)"
  else
    # Keep runtime/ and state/ (logs, cost ledgers) unless --purge; remove code.
    for item in \
      reasonix-native-gateway.py reasonix-fleet-mcp.py \
      bridge-settings.json system-prompt-reasonix.md ; do
      rm -f "$INSTALL_HOME/$item"
    done
    rm -rf "$INSTALL_HOME/hooks"
    ok "removed fleet code from $INSTALL_HOME (kept runtime/ and state/ — pass --purge to delete)"
  fi
else
  warn "no fleet at $INSTALL_HOME"
fi

say "Done"
ok "claude-reasonix uninstalled."
echo "  claude and node were left untouched (we never installed them)."
echo "  The engine was the bundled in-process fork — nothing external to revert."
