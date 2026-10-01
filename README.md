# agent-tts-macos

Hear your coding agents. Reads aloud, on macOS, what **Claude Code**, **Codex** and **Cursor** write in chat — the progress notes between tool calls and the final answer — with a natural voice that pronounces English technical terms correctly in the middle of another language.

> 🇧🇷 O projeto nasceu em português: a interface, os comandos e a voz padrão são PT-BR. A seção [Em português](#em-português) resume a instalação. Para outro idioma, veja [Other languages](#other-languages).

- **Natural, code-switching voice** — Google Gemini TTS, steered by a style prompt ("pronounce English terms like a native").
- **Starts in ~2 seconds**, even for long answers: audio is streamed and played as it arrives.
- **Pause anywhere with ⌥F** — a global shortcut pauses and resumes exactly where it stopped.
- **Never silent, never overlapping** — one playback queue and one audio output; if Gemini fails it falls back to Microsoft's free neural voices, then to the macOS `say` voice.
- **Menu bar control** — on/off, voice (30 options), speed, final answers only or with progress notes, stop, repeat, and **Speak now** (finds the latest final answer in any of the three tools).
- Code blocks, URLs, emojis and file paths are cleaned up before speaking.
- **Plan usage at the end** — after each final answer it says the remaining % of your plan: weekly for Claude Code and Codex, monthly for Cursor (just the number, e.g. "65"). Turn off with `"falar_limite": false`.

## Requirements

- macOS (Apple Silicon or Intel) with [Homebrew](https://brew.sh): `brew install python ffmpeg`
- At least one of: Claude Code (CLI or desktop), Codex (CLI, IDE or desktop app), Cursor.
- Optional but recommended: a **Gemini API key with billing enabled**. The free tier allows only ~10 TTS requests per day, which is not enough. Cost is about US$ 0.80 per hour of speech (Gemini 3.8 Flash TTS, 2026 pricing). Without a key, the free Microsoft voice is used. Even when paying, Tier 1 accounts are capped at about 100 TTS requests per day per model; when Flash hits it, the app switches to Flash-Lite (separate quota) and only then to Microsoft, until Google's reset time.

## Install

```bash
git clone https://github.com/gabrielpc4/agent-tts-macos.git
cd agent-tts-macos
./install.sh
```

The installer:

1. copies the app to `~/.tts-agentes` and creates a Python venv there (`edge-tts`, `rumps`);
2. adds a `tts` command to `~/.local/bin`;
3. **merges** hooks into `~/.claude/settings.json`, `~/.codex/hooks.json` and `~/.cursor/hooks.json` (existing settings are kept; a `.bak-tts` backup is made once);
4. starts the 🔊 menu bar app and registers it to open at login.

Then:

- **Gemini key:** create one at <https://aistudio.google.com/apikey>, enable billing on its project, copy the key and run `~/.tts-agentes/salvar_chave.sh tts-gemini`. The key goes to the macOS Keychain (never to a file) and the clipboard is cleared.
- **Codex:** new hooks must be approved once — open Codex and use `/hooks`.
- **Test:** `tts testar` speaks a sample sentence. `tts autoteste` runs a full diagnostic.

Re-running `./install.sh` is safe (it updates in place). `./uninstall.sh` removes everything except the Keychain entry.

## Usage

The 🔊 icon (🗣️ while speaking, 🔇 when off):

| Item | What it does |
|---|---|
| Pausar / continuar (⌥F) | Pause and resume from the exact point; ⌥F works in any app |
| Falar agora (Speak now) | Reads the most recent final answer from Claude Code, Codex or Cursor, even with reading turned off |
| Leitura ligada | Turns automatic reading on/off |
| Ler passos intermediários | Off = read only final answers, skipping progress notes (`tts intermediarias off`) |
| Parar fala / Repetir última / Testar voz | Stop, replay (no API cost), sample |
| Voz | 30 Gemini voices, female and male |
| Velocidade | 1.0x–2.0x extra speed on top of the "fast" pace requested from Gemini (pitch preserved) |
| Modelo Gemini | Flash (best) or Flash-Lite (cheaper) |

Command line equivalents: `tts`, `tts on|off`, `tts voz Kore`, `tts vozes`, `tts velocidade 1.3`, `tts intermediarias off`, `tts agora`, `tts pausa`, `tts parar`, `tts repetir`, `tts log`, `tts autoteste`.

## How it works

```
agent hook ──► tts.py hook ──► queue (~/.tts-agentes/fila) ──► single worker
                (cleans text,      one file per utterance        ├─ generates the next items ahead
                 dedupes)                                        ├─ Gemini stream → audio output (PCM, pausable)
                                                                 ├─ fallback: Microsoft Edge TTS (MP3)
                                                                 └─ fallback: macOS say
```

| Tool | Progress notes | Final answer |
|---|---|---|
| Claude Code | `PostToolUse` hook reads new assistant text from the transcript | `Stop` hook (`last_assistant_message`) |
| Codex | `PostToolUse` hook reads the rollout transcript | `Stop` hook |
| Cursor | — | `afterAgentResponse` hook |

Claude Code's desktop app saves some progress notes as "narration" blocks; those are read too, while real reasoning blocks never are.

Long texts are split into ~1500-character parts and streamed back to back. Gemini rate limits (HTTP 429) pause Gemini automatically and the fallback voice is used meanwhile; the menu shows it.

## Configuration

`~/.tts-agentes/config.json` (created on first change; edit or use the menu):

| Key | Default | Notes |
|---|---|---|
| `voz` | `Kore` | Any Gemini prebuilt voice |
| `velocidade` | `1.0` | 1.0–2.0 |
| `intermediarias` | `true` | `false` reads only final answers |
| `modelo` | `gemini-3.8-flash-tts` | or `gemini-3.8-flash-lite-tts` |
| `max_caracteres` | `3000` | Longer texts are cut at a sentence boundary |
| `falar_limite` | `true` | Say the remaining plan % after final answers |
| `silencio_inicial_ms` | `600` | Silence played before each utterance so the speakers wake up and the first word isn't swallowed |
| `estilo` | PT-BR style prompt | Instruction sent to Gemini (language, pace, pronunciation) |
| `voz_reserva` | `pt-BR-ThalitaMultilingualNeural` | Microsoft fallback voice (`edge-tts --list-voices`) |
| `voz_offline` | `Luciana` | macOS voice (`say -v '?'`) |

### Other languages

Set `estilo`, `voz_reserva` and `voz_offline` for your language, for example English:

```json
{
  "estilo": "Speak in natural American English at a brisk pace, like a colleague explaining what they just did.",
  "voz_reserva": "en-US-AvaMultilingualNeural",
  "voz_offline": "Samantha"
}
```

## Writing for the ear

Agents write for screens: tables, paths, symbols. [`docs/regra-mensagens.md`](docs/regra-mensagens.md) is a template of instructions (for `CLAUDE.md`, `AGENTS.md` or Cursor User Rules) that makes their messages pleasant to listen to.

## Privacy

The text of your agents' messages is sent to Google (Gemini) or Microsoft (fallback) to be synthesized. Code blocks and URLs are stripped first, but anything else the agent writes in chat is sent. Don't use it if that's not acceptable for your work.

## Troubleshooting

- `tts log` shows each utterance with the engine used and the time to first audio.
- `tts autoteste` checks audio integrity (no gaps), that two sounds never overlap and the gap between utterances.
- Nothing from Codex? Approve the hooks with `/hooks`.
- Menu bar icon missing? `launchctl kickstart -k gui/$(id -u)/com.tts-agentes.menubar`, errors in `~/.tts-agentes/menubar.err`.

## Em português

Lê em voz alta, no Mac, o que o Claude Code, o Codex e o Cursor escrevem no chat, com voz natural que pronuncia termos em inglês do jeito certo. Instale com `./install.sh`, salve a chave do Gemini com `~/.tts-agentes/salvar_chave.sh tts-gemini` (o plano grátis só permite 10 pedidos por dia; ative o faturamento) e aprove os hooks no Codex com `/hooks`. Tudo se controla pelo ícone 🔊 na barra de menus.

## License

MIT
