#!/bin/zsh
# Remove o agent-tts-macos. A chave no Keychain fica; para apagar:
#   security delete-generic-password -s tts-gemini
set -e
BASE="$HOME/.tts-agentes"
LABEL="com.tts-agentes.menubar"
[[ -x "$BASE/.venv/bin/python" ]] && "$BASE/.venv/bin/python" "$BASE/tts.py" parar >/dev/null 2>&1 || true
[[ -f "$BASE/configurar_hooks.py" ]] && python3 "$BASE/configurar_hooks.py" remover
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist" "$HOME/.local/bin/tts"
rm -rf "$BASE"
echo "Removido."
