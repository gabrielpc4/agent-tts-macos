#!/bin/zsh
# Removes agent-tts-macos. The Keychain key stays; to delete it:
#   security delete-generic-password -s tts-gemini
set -e
BASE="$HOME/.agent-tts"
LABEL="com.agent-tts.menubar"
[[ -x "$BASE/.venv/bin/python" ]] && "$BASE/.venv/bin/python" "$BASE/tts.py" stop >/dev/null 2>&1 || true
[[ -f "$BASE/configure_hooks.py" ]] && python3 "$BASE/configure_hooks.py" remove
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist" "$HOME/.local/bin/tts"
rm -rf "$BASE"
echo "Removed."
