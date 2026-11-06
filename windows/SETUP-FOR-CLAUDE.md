# Windows setup guide (for the Claude doing the setup)

You are Claude running on Gabriel's Windows PC, and he asked **you** to install and configure all of this, without him adjusting anything by hand. This guide says what to do, how to check it and where the risk is.

## What it is

A tool that reads Claude Code's replies out loud (and, if present, Codex and Cursor), using the Gemini TTS voice, plus a tray icon to control it. It already runs on Gabriel's Mac and was ported to Windows **without ever having been run on Windows**: the code was written and reviewed on the Mac, and only the system-independent logic was tested there. **You are the one who will find out what really breaks.** Expect small adjustments.

There are also two separate things to install: the voice tool (the `tts` folder) and the Claude Code skills (the `skills-windows` folder), which only exist in the package Gabriel handed you. If the `skills-windows` folder is not next to this guide, install only the tool and tell Gabriel the skills were left out.

## Rules for you

- You can and should **fix the tool's code** if something fails on Windows. Keep macOS working: the same code runs on both, and the differences live in `platform_support.py` and `win_integration.py`.
- **Never show, print or log the Gemini key.** It only enters through the `tts key gemini` command, which reads the clipboard and stores it in Credential Manager.
- Don't touch hooks, skills or rules of other tools beyond what is described here.
- Before running the audio test, warn Gabriel that it will play sound for about 15 seconds.
- At the end, hand over a short summary of what worked, what you had to fix (with the diff of the changed files) and what is still pending.

## Step by step

1. **Prerequisites.** Python 3.10 or newer, `ffmpeg` on the PATH and Git for Windows (Claude Code hooks run in its bash). If anything is missing: `winget install Python.Python.3.12`, `winget install Gyan.FFmpeg`, `winget install Git.Git`. The installer already tries ffmpeg on its own.

2. **Install the tool.** In the `tts` folder:
   ```powershell
   powershell -ExecutionPolicy Bypass -File windows\install.ps1
   ```
   It copies the files to `%USERPROFILE%\.agent-tts`, creates the Python environment, creates the `tts` command, configures the hooks in `%USERPROFILE%\.claude\settings.json` and puts the tray icon in place to open at login. If the freshly installed `ffmpeg` is not recognized, open a new terminal and run it again.

3. **Install the skills** (if `skills-windows` exists). In that folder:
   ```powershell
   powershell -ExecutionPolicy Bypass -File instalar.ps1
   ```
   This copies the four skills (`speak-work`, `speak-personal`, `read-work`, `read-personal`) to `%USERPROFILE%\.claude\skills` and appends the global rule block to `CLAUDE.md`, without erasing the rest.

4. **Gemini key.** If a `key` folder exists next to this guide, it contains `tts-gemini.txt` with Gabriel's key. Import it without displaying it:
   ```powershell
   tts key gemini key\tts-gemini.txt
   ```
   The command stores the key in Credential Manager and **deletes the file**. Check only the success message (4 characters and the length); never open, print or copy the file's contents. If the `key` folder doesn't exist, ask Gabriel to create or copy the key at https://aistudio.google.com/apikey (the project needs billing enabled; the free plan only allows 10 requests per day), copy it and tell you; then run `tts key gemini`, which reads the clipboard. Without the key the tool uses the free Microsoft voice, so you can validate the rest first.

## Checks (do all of them and report)

1. `tts doctor`: ffmpeg, ffprobe, audio output, edge-tts, key, hooks and dependencies. Everything must be ✅.
2. `tts selftest`: records three utterances to a file, looks for silences in the middle, then plays three utterances live and checks that they finish without errors. It plays sound.
3. **Tray icon.** Look for the round icon near the clock (maybe under "hidden icons"). Check that the menu opens with Voice language as the first item (it defaults to the Windows display language, or English if that isn't one of the supported languages), followed by Voice, Speed and Gemini model, and that **Reading on** starts unchecked (the tool always starts off).
4. **Claude Code hook.** Turn reading on, ask for anything short in a Claude Code conversation and check `tts log` for a `hook source=claude` line. If the log shows nothing, the hook didn't run: look at the command in `%USERPROFILE%\.claude\settings.json` and run it by hand in the Git Bash terminal and in PowerShell to see the error. Remember that the hooks use forward slashes and quotes around the paths.
5. **Global shortcuts.** `Ctrl+Alt+P` pauses and resumes speech; `Ctrl+Alt+S` reads the selected text. If `tts log` says a shortcut is unavailable, another program is using it: change `pause_shortcut` or `selection_shortcut` in `%USERPROFILE%\.agent-tts\config.json`. ABNT2 keyboard: `Ctrl+Alt` equals AltGr, so prefer combinations that don't type symbols.
6. **Pause and resume.** During a long utterance (`tts say "long sentence..."`), pause and resume through the shortcut and through the menu; it must continue from the exact point, without repeating or skipping.
7. **Speak selection** in an ordinary app (Notepad or the browser): select, press the shortcut, listen; the clipboard goes back to what it was.

## Where it's most likely to break

| Area | Symptom | Where to look |
|---|---|---|
| Hook doesn't fire | `tts log` empty after a reply | command in `settings.json`; Git Bash on the PATH; slashes and quotes |
| Flashing console | a black window appears on every utterance | `platform_support.no_window()` and the flags in `start_detached` |
| No sound or odd sound | `doctor` shows the wrong device | `sounddevice` uses the Windows default output |
| Offline voice fails | `tts` falls to silence with neither Gemini nor Microsoft | `platform_support.offline_voice_pcm` uses SAPI; the offline voice follows the selected voice language ("Microsoft Zira Desktop" for English, "Microsoft Maria Desktop" for Brazilian Portuguese) and requires that Windows language pack to be installed; list the voices with `Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices()` and override with `offline_voice` in `config.json` |
| Doesn't stop or pause | `Stop speaking` doesn't cut the sound | `platform_support.kill_tree` and the worker lock (`FileLock`, using `msvcrt`) |
| Shortcut doesn't work | nothing happens when pressed | `win_integration.register_hotkeys` and key combination conflicts |
| Speak selection empty | "nothing selected" | `win_integration.selected_text` (simulated Ctrl+C); apps running with elevated permissions don't accept simulated input |
| Key isn't read | `tts doctor` shows ❌ on the key after saving | `keyring` and Credential Manager; the service is `agent-tts`, the account is `tts-gemini` |
| Broken accents | `?` or odd characters in the log or in the utterances | everything uses explicit UTF-8; check `platform_support.prepare_console` |

## How the code is organized

- `tts.py`: the core, the same on both systems. Utterance queue, Gemini streaming audio generation, the Microsoft fallback voice and the system voice, hooks, "Speak now", "Speak selection".
- `platform_support.py`: everything that differs between Mac and Windows (key vault, file lock, processes, offline voice, paths).
- `win_integration.py`: clipboard, simulated keys and global shortcuts, using only `ctypes`.
- `tray_windows.py`: the tray icon and menu (on the Mac it's `menubar.py`).
- `configure_hooks.py`: installs and removes the hooks (`install|remove`).
- `selftest.py`: the audio test.

## What's different on Windows compared to the Mac

| | Mac | Windows |
|---|---|---|
| Key | Login Keychain | Credential Manager |
| Offline voice | `say` (follows the voice language) | SAPI (Microsoft Zira / Maria Desktop, follows the voice language) |
| Pause | ⌥P | Ctrl+Alt+P |
| Speak selection | ⌥Esc | Ctrl+Alt+S |
| Icon | menu bar | system tray |
| Opens at login | LaunchAgent | shortcut ("Agent TTS.lnk") in the Startup folder |
| iOS app testing | Simulator + Maestro | doesn't exist: browser only |

(The install folder is `%USERPROFILE%\.agent-tts` on Windows and `~/.agent-tts` on the Mac.)

## If you need to undo

`powershell -ExecutionPolicy Bypass -File windows\uninstall.ps1` removes the tool, the hooks and the icon. The key in Credential Manager stays.
