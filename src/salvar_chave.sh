#!/bin/zsh
# Salva a chave que está na área de transferência no Keychain do macOS e limpa o clipboard.
# Uso: ./salvar_chave.sh tts-elevenlabs   |   ./salvar_chave.sh tts-gemini
set -e
servico="$1"
[[ "$servico" == tts-elevenlabs || "$servico" == tts-gemini ]] || { echo "uso: $0 tts-elevenlabs|tts-gemini"; exit 1; }
chave="$(pbpaste | tr -d '[:space:]')"
[[ ${#chave} -ge 20 ]] || { echo "Clipboard não parece conter uma chave. Clique em Copy primeiro."; exit 1; }
if [[ "$servico" == tts-elevenlabs && "$chave" != sk_* ]]; then
  echo "❌ Isso não parece uma chave do ElevenLabs (elas começam com sk_). Nada foi salvo."; exit 1
fi
if [[ "$servico" == tts-gemini && "$chave" == sk_* ]]; then
  echo "❌ Isso parece uma chave do ElevenLabs, não do Gemini. Nada foi salvo."; exit 1
fi
security add-generic-password -U -a "$USER" -s "$servico" -w "$chave"
printf '' | pbcopy
echo "✅ $servico salva no Keychain (${chave:0:4}…, ${#chave} caracteres). Clipboard limpo."
