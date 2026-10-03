#!/bin/zsh
# Instala o agent-tts-macos: leitura em voz alta das respostas do Claude Code, Codex e Cursor.
set -e
cd "$(dirname "$0")"
BASE="$HOME/.tts-agentes"
LABEL="com.tts-agentes.menubar"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

[[ "$(uname)" == Darwin ]] || { echo "Só funciona no macOS."; exit 1; }
for bin in python3 ffmpeg; do
  command -v $bin >/dev/null || { echo "Falta '$bin'. Instale com: brew install python ffmpeg"; exit 1; }
done

echo "→ Copiando arquivos para $BASE"
mkdir -p "$BASE"
cp src/tts.py src/menubar.py src/autoteste.py src/salvar_chave.sh src/configurar_hooks.py "$BASE/"
chmod +x "$BASE/salvar_chave.sh"

echo "→ Criando o ambiente Python (edge-tts, rumps, sounddevice, quickmachotkey)"
[[ -x "$BASE/.venv/bin/python" ]] || python3 -m venv "$BASE/.venv"
"$BASE/.venv/bin/pip" install -q --upgrade edge-tts rumps sounddevice quickmachotkey pyobjc-framework-ApplicationServices pyobjc-framework-Quartz

echo "→ Comando 'tts' em ~/.local/bin"
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/tts" <<EOT
#!/bin/zsh
exec "$BASE/.venv/bin/python" "$BASE/tts.py" "\$@"
EOT
chmod +x "$HOME/.local/bin/tts"
[[ ":$PATH:" == *":$HOME/.local/bin:"* ]] || echo "  ⚠️  Adicione ~/.local/bin ao PATH para usar o comando 'tts'."

echo "→ Hooks do Claude Code, Codex e Cursor"
"$BASE/.venv/bin/python" "$BASE/configurar_hooks.py" instalar

echo "→ Ícone na barra de menus (abre no login)"
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
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
for i in {1..20}; do  # o bootout é assíncrono: espera o serviço sair antes de recarregar
  launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || break
  sleep 0.25
done
for i in {1..5}; do
  launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null && break
  sleep 1
done
launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || { echo "  ⚠️  Não consegui iniciar o ícone da barra de menus."; exit 1; }

echo
echo "Pronto. Próximos passos:"
if security find-generic-password -s tts-gemini "$HOME/Library/Keychains/login.keychain-db" >/dev/null 2>&1; then
  echo "  • Chave do Gemini já está no Keychain."
else
  echo "  • Crie uma chave em https://aistudio.google.com/apikey, copie e rode:"
  echo "      $BASE/salvar_chave.sh tts-gemini"
  echo "    (sem chave, a voz usada é a da Microsoft, grátis)"
fi
echo "  • No Codex, aprove os hooks novos com /hooks."
echo "  • Para o Falar seleção (⌥Esc), libere o Python em Ajustes > Privacidade e Segurança > Acessibilidade."
echo "  • Teste: tts testar   ·   Diagnóstico completo: tts autoteste"
