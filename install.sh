#!/bin/zsh
# Installs agent-tts-macos: reads Claude Code, Codex and Cursor answers aloud.
set -e
cd "$(dirname "$0")"
BASE="$HOME/.agent-tts"
LABEL="com.agent-tts.menubar"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
# Folder and menu bar service used before the English rename: migrated and removed below.
OLD_BASE="$HOME/.tts-agentes"
OLD_LABEL="com.tts-agentes.menubar"

[[ "$(uname)" == Darwin ]] || { echo "macOS only."; exit 1; }
for bin in python3 ffmpeg; do
  command -v $bin >/dev/null || { echo "Missing '$bin'. Install it with: brew install python ffmpeg"; exit 1; }
done

stop_service() {
  launchctl bootout "gui/$(id -u)/$1" 2>/dev/null || true
  for i in {1..20}; do  # bootout is asynchronous: wait for the service to exit before reloading
    launchctl print "gui/$(id -u)/$1" >/dev/null 2>&1 || break
    sleep 0.25
  done
}

mkdir -p "$BASE"
if [[ -d "$OLD_BASE" ]]; then
  echo "→ Migrating the previous install ($OLD_BASE)"
  [[ -x "$OLD_BASE/.venv/bin/python" ]] && "$OLD_BASE/.venv/bin/python" "$OLD_BASE/tts.py" parar >/dev/null 2>&1 || true
  stop_service "$OLD_LABEL"
  rm -f "$HOME/Library/LaunchAgents/$OLD_LABEL.plist"
  if [[ -f "$OLD_BASE/config.json" && ! -f "$BASE/config.json" ]]; then
    cp "$OLD_BASE/config.json" "$BASE/config.json"  # tts.py translates the old keys when it reads it
  fi
fi

echo "→ Copying files to $BASE"
cp src/tts.py src/platform_support.py src/menubar.py src/selftest.py src/save_key.sh src/configure_hooks.py "$BASE/"
chmod +x "$BASE/save_key.sh"

echo "→ Creating the Python environment (edge-tts, rumps, sounddevice, quickmachotkey)"
[[ -x "$BASE/.venv/bin/python" ]] || python3 -m venv "$BASE/.venv"
"$BASE/.venv/bin/pip" install -q --upgrade edge-tts rumps sounddevice quickmachotkey pyobjc-framework-ApplicationServices pyobjc-framework-Quartz

echo "→ 'tts' command in ~/.local/bin"
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/tts" <<EOT
#!/bin/zsh
exec "$BASE/.venv/bin/python" "$BASE/tts.py" "\$@"
EOT
chmod +x "$HOME/.local/bin/tts"
[[ ":$PATH:" == *":$HOME/.local/bin:"* ]] || echo "  ⚠️  Add ~/.local/bin to your PATH to use the 'tts' command."

echo "→ Claude Code, Codex and Cursor hooks"
"$BASE/.venv/bin/python" "$BASE/configure_hooks.py" install

if [[ -d "$OLD_BASE" ]]; then
  rm -rf "$OLD_BASE"  # its hooks were replaced above; the config was copied
fi

echo "→ Menu bar icon (opens at login)"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOT
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$BASE/.venv/bin/python</string>
    <string>$BASE/menubar.py</string>
  </array>
  <key>WorkingDirectory</key><string>$BASE</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>StandardErrorPath</key><string>$BASE/menubar.err</string>
</dict>
</plist>
EOT
stop_service "$LABEL"
for i in {1..5}; do
  launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null && break
  sleep 1
done
launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || { echo "  ⚠️  Couldn't start the menu bar icon."; exit 1; }

echo
echo "Done. Next steps:"
if security find-generic-password -s tts-gemini "$HOME/Library/Keychains/login.keychain-db" >/dev/null 2>&1; then
  echo "  • The Gemini key is already in the Keychain."
else
  echo "  • Create a key at https://aistudio.google.com/apikey, copy it and run:"
  echo "      $BASE/save_key.sh tts-gemini"
  echo "    (without a key, the free Microsoft voice is used)"
fi
echo "  • In Codex, approve the new hooks with /hooks."
echo "  • For Speak selection (⌥Esc), allow Python in System Settings > Privacy & Security > Accessibility."
echo "  • The voice language follows your Mac's language; change it in the menu (first item) or with: tts language"
echo "  • Test: tts test   ·   Full diagnostic: tts selftest"
