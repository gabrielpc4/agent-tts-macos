#!/bin/zsh
# Saves the key on the clipboard to the macOS Keychain and clears the clipboard.
# Usage: ./save_key.sh tts-elevenlabs   |   ./save_key.sh tts-gemini
set -e
service="$1"
[[ "$service" == tts-elevenlabs || "$service" == tts-gemini ]] || { echo "usage: $0 tts-elevenlabs|tts-gemini"; exit 1; }
key="$(pbpaste | tr -d '[:space:]')"
[[ ${#key} -ge 20 ]] || { echo "The clipboard doesn't seem to hold a key. Click Copy first."; exit 1; }
if [[ "$service" == tts-elevenlabs && "$key" != sk_* ]]; then
  echo "❌ This doesn't look like an ElevenLabs key (they start with sk_). Nothing was saved."; exit 1
fi
if [[ "$service" == tts-gemini && "$key" == sk_* ]]; then
  echo "❌ This looks like an ElevenLabs key, not a Gemini one. Nothing was saved."; exit 1
fi
# Always in the login keychain: the Mac's default may be a temporary keychain (fastlane, EAS)
security add-generic-password -U -a "$USER" -s "$service" -w "$key" "$HOME/Library/Keychains/login.keychain-db"
printf '' | pbcopy
echo "✅ $service saved in the Keychain (${key:0:4}…, ${#key} characters). Clipboard cleared."
