# agent-tts-macos

Hear your coding agents. Reads aloud, on macOS and Windows, what **Claude Code**, **Codex** and **Cursor** write in chat — the progress notes between tool calls and the final answer — with a natural voice that pronounces English technical terms correctly in the middle of another language.

- **12 voice languages** — English (US, UK), Portuguese (Brazil, Portugal), Spanish (Spain, Mexico), French, German, Italian, Japanese, Chinese and Korean. It starts in your system's language (English when yours isn't one of them) and is the first item of the menu.
- **Natural, code-switching voice** — Google Gemini TTS, steered by a style prompt ("pronounce English terms like a native").
- **Starts in ~2 seconds**, even for long answers: audio is streamed and played as it arrives.
- **Speak selection with ⌥Esc** — like macOS Speak Selection, but with this app's voice and settings; press again to stop.
- **Pause anywhere with ⌥P** — a global shortcut pauses and resumes exactly where it stopped.
- **Never silent, never overlapping** — one playback queue and one audio output; if Gemini fails it falls back to Microsoft's free neural voices, then to the system voice (`say` on macOS).
- **Menu bar control** — voice language, on/off, voice (30 options), speed, final answers only or with progress notes, stop, repeat, and **Speak now** (finds the latest final answer in any of the three tools).
- Code blocks, URLs, emojis and file paths are cleaned up before speaking.

## Requirements

- macOS (Apple Silicon or Intel) with [Homebrew](https://brew.sh): `brew install python ffmpeg` — or Windows 10/11, see [Windows](#windows)
- At least one of: Claude Code (CLI or desktop), Codex (CLI, IDE or desktop app), Cursor.
- Optional but recommended: a **Gemini API key with billing enabled**. The free tier allows only ~10 TTS requests per day, which is not enough. Cost is about US$ 0.80 per hour of speech (Gemini 3.8 Flash TTS, 2026 pricing). Without a key, the free Microsoft voice is used. Even when paying, Tier 1 accounts are capped at about 100 TTS requests per day per model; when Flash hits it, the app switches to Flash-Lite (separate quota) and only then to Microsoft, until Google's reset time.

## Install

```bash
git clone https://github.com/gabrielpc4/agent-tts-macos.git
cd agent-tts-macos
./install.sh
```

The installer:

1. copies the app to `~/.agent-tts` and creates a Python venv there (`edge-tts`, `rumps`);
2. adds a `tts` command to `~/.local/bin`;
3. **merges** hooks into `~/.claude/settings.json`, `~/.codex/hooks.json` and `~/.cursor/hooks.json` (existing settings are kept; a `.bak-tts` backup is made once);
4. starts the 🔊 menu bar app and registers it to open at login.

Upgrading from a version that installed to `~/.tts-agentes`: just run `./install.sh` again. It moves your settings over, replaces the old hooks and menu bar service, and removes the old folder.

Then:

- **Gemini key:** create one at <https://aistudio.google.com/apikey>, enable billing on its project, copy the key and run `~/.agent-tts/save_key.sh tts-gemini` (or `tts key gemini`). The key goes to the macOS Keychain (never to a file) and the clipboard is cleared.
- **Codex:** new hooks must be approved once — open Codex and use `/hooks`.
- **Test:** `tts test` speaks a sample sentence in the voice language. `tts selftest` runs a full diagnostic.

Re-running `./install.sh` is safe (it updates in place). `./uninstall.sh` removes everything except the Keychain entry.

## Windows

There is a Windows port: system tray icon, global shortcuts (Ctrl+Alt+P pause, Ctrl+Alt+S speak selection), Windows Credential Manager for the key, and SAPI as the offline voice. **It was written on a Mac and has not been run on Windows yet**, so expect small fixes.

```powershell
powershell -ExecutionPolicy Bypass -File windows\install.ps1
tts key gemini     # after copying your Gemini key
tts doctor         # checks ffmpeg, audio, key, hooks and dependencies
tts selftest       # plays three test utterances
```

Needs Python 3.10+, ffmpeg and Git for Windows (Claude Code runs hooks in its bash). Full step-by-step and a troubleshooting table, written so another Claude can set everything up: [`windows/SETUP-FOR-CLAUDE.md`](windows/SETUP-FOR-CLAUDE.md).

## Usage

The 🔊 icon (🗣️ while speaking, 🔇 when off, ⏸️ when paused):

| Item | What it does |
|---|---|
| Voice language | The language the voice speaks: "System language" follows your Mac's language (English if it isn't supported), or pick one. Changing it plays a sample (`tts language pt-BR`, `tts languages`) |
| Speak selection (⌥Esc) | Reads the selected text in the frontmost app with this voice; again to stop. Needs Accessibility permission for Python |
| Pause / resume (⌥P) | Pause and resume from the exact point; ⌥P works in any app |
| Speak now | Reads the most recent final answer from Claude Code, Codex or Cursor, even with reading turned off |
| Reading on | Turns automatic reading on/off. The app always starts off |
| Read progress notes | Off = read only final answers, skipping progress notes (`tts progress off`) |
| Read Codex subagents | Off by default: answers from Codex subagents are ignored (`tts subagents on`) |
| Conversations | Lists the conversations that are active now (Claude Code, Codex, Cursor) with their title and folder: a conversation leaves the list when its session ends, when its Claude Code process is gone, or after 2 hours without any event (30 minutes when there is no process to check), and comes back with its next event. Headless runs (`claude -p`, Agent SDK) never show up and are never read aloud. Tick the ones you want to hear: ticking switches on "Hear only the chosen ones", and every other conversation goes silent. Untick that option to hear all of them again (`tts conversations`, `tts conversations on\|off`, `tts listen 2`) |
| Stop speaking / Repeat last / Test voice | Stop, replay (no API cost), sample |
| Voice | 30 Gemini voices, female and male |
| Speed | 1.0x–2.0x extra speed on top of the "fast" pace requested from Gemini (pitch preserved) |
| Gemini model | Flash (best) or Flash-Lite (cheaper) |

Command line equivalents: `tts`, `tts on|off`, `tts language [code|system]`, `tts languages`, `tts voice Kore`, `tts voices`, `tts speed 1.3`, `tts faster|slower`, `tts progress off`, `tts now`, `tts selection`, `tts pause`, `tts stop`, `tts repeat`, `tts say "text"`, `tts log`, `tts doctor`, `tts selftest`.

## How it works

```
agent hook ──► tts.py hook ──► queue (~/.agent-tts/queue) ──► single worker
                (cleans text,      one file per utterance        ├─ generates the next items ahead
                 dedupes per turn)                               ├─ Gemini stream → audio output (PCM, pausable)
                                                                 ├─ fallback: Microsoft Edge TTS (MP3)
                                                                 └─ fallback: system voice (say / SAPI)
```

| Tool | Progress notes | Final answer |
|---|---|---|
| Claude Code | `PreToolUse` and `PostToolUse` hooks read new assistant text from the transcript | `Stop` hook (`last_assistant_message`) |
| Codex | `PreToolUse` and `PostToolUse` hooks read the rollout transcript | `Stop` hook |
| Cursor | — | `afterAgentResponse` hook |

A progress note is queued as soon as the next tool starts (`PreToolUse`), not when it finishes, so a long command doesn't hold the note back until the agent has already written the next one. `PostToolUse` catches a note that reached the transcript late. Each text is spoken once per turn: these hooks and `Stop` see the same texts, and only the new ones are queued. The same reply in a later turn is spoken again.

Claude Code's desktop app saves some progress notes as "narration" blocks; those are read too, while real reasoning blocks never are.

Long texts are split into ~3000-character parts and streamed back to back. Gemini rate limits (HTTP 429) pause Gemini automatically and the fallback voice is used meanwhile; the menu shows it.

## Configuration

`~/.agent-tts/config.json` (created on first change; edit or use the menu):

| Key | Default | Notes |
|---|---|---|
| `language` | `""` | `""` follows the system language; or one of `tts languages` (`en-US`, `pt-BR`, `ja-JP`…) |
| `voice` | `Kore` | Any Gemini prebuilt voice |
| `speed` | `1.0` | 1.0–2.0 |
| `progress_notes` | `true` | `false` reads only final answers |
| `codex_subagents` | `false` | `true` also reads Codex subagents |
| `model` | `gemini-3.8-flash-tts` | or `gemini-3.8-flash-lite-tts` |
| `max_characters` | `0` | 0 reads the whole answer (each 3000 characters is one request); a number cuts the speech at a sentence boundary |
| `lead_silence_ms` | `600` | Silence played before each utterance so the speakers wake up and the first word isn't swallowed |
| `style` | `""` | Instruction sent to Gemini (language, pace, pronunciation); `""` uses the one built for the voice language |
| `fallback_voice` | `""` | Microsoft fallback voice (`edge-tts --list-voices`); `""` uses the one of the voice language |
| `offline_voice` | `""` | System voice (`say -v '?'` on macOS); `""` uses the one of the voice language |

To add a language, add an entry to `LANGUAGES` in `src/tts.py` (its own name, English name, Microsoft voice, system voices and a test sentence).

## Writing for the ear

Agents write for screens: tables, paths, symbols. [`docs/writing-for-the-ear.md`](docs/writing-for-the-ear.md) is a template of instructions (for `CLAUDE.md`, `AGENTS.md` or Cursor User Rules) that makes their messages pleasant to listen to.

## Privacy

The text of your agents' messages is sent to Google (Gemini) or Microsoft (fallback) to be synthesized. Code blocks and URLs are stripped first, but anything else the agent writes in chat is sent. Don't use it if that's not acceptable for your work.

## Troubleshooting

- `tts log` shows each utterance with the engine used and the time to first audio.
- `tts selftest` checks audio integrity (no gaps), that two sounds never overlap and the gap between utterances.
- Nothing from Codex? Approve the hooks with `/hooks`.
- Menu bar icon missing? `launchctl kickstart -k gui/$(id -u)/com.agent-tts.menubar`, errors in `~/.agent-tts/menubar.err`.

## License

MIT
