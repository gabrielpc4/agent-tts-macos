#!/usr/bin/env python3
"""tts — reads Claude Code, Codex and Cursor answers aloud.

Commands:
  tts                         show the status
  tts on | off                turn reading on / off
  tts language [code]         show or change the voice language (e.g. tts language en-US)
  tts languages               list the voice languages
  tts voice [Name]            show or change the Gemini voice (e.g. tts voice Kore)
  tts voices                  list the available voices
  tts speed [x]               show or set the extra speed-up (1.0 to 2.0; 1.0 = the base "fast" pace)
  tts faster | slower         +0.1 / -0.1 on the speed
  tts progress on|off         read or skip the progress notes in the middle of the work
  tts subagents on|off        read or ignore the answers of Codex subagents
  tts conversations [on|off]  list the active conversations; on = only the chosen ones speak, off = all of them
  tts listen <n|all|none>     choose (or unchoose) a conversation from the list, by its number in "tts conversations"
  tts selection               read the text selected in the frontmost app (global shortcut: ⌥Esc)
  tts pause                   pause or resume speech where it stopped (global shortcut: ⌥P)
  tts stop                    stop the current speech
  tts now                     read the latest final answer now (Claude Code, Codex or Cursor)
  tts repeat                  repeat the last speech (at the current speed, without calling the API)
  tts test                    speak a test sentence
  tts say "text"              speak any text
  tts log                     show the last lines of the log
  tts key gemini [file]       store the copied key in the system vault, or read it from a file (deleted afterwards)
  tts doctor                  check ffmpeg, audio, key, hooks and dependencies
  tts selftest                check integrity, overlap and silence between utterances

Internal use (hooks):  tts hook claude|codex stop|tool|end  ·  tts hook cursor   (hook JSON on stdin)
"""
import base64
import glob
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import wave
from datetime import datetime
from pathlib import Path

import platform_support
from platform_support import WINDOWS, FileLock, no_window

platform_support.prepare_console()

BASE = Path.home() / ".agent-tts"
CONFIG = BASE / "config.json"
PIDFILE = BASE / "worker.pid"
LOCK = BASE / "worker.lock"
QUEUE = BASE / "queue"
STATE = BASE / "state"
LOG = BASE / "tts.log"
LAST = BASE / "last"  # last.wav / last.mp3
PYTHON = platform_support.venv_python(BASE)

DEFAULTS = {
    "enabled": True,
    "language": "",              # "" = the system language (English when it isn't one of LANGUAGES)
    "voice": "Kore",
    "model": "gemini-3.8-flash-tts",  # or gemini-3.8-flash-lite-tts (cheaper)
    "speed": 1.0,                # extra speed-up applied locally
    "codex_subagents": False,    # True: also read what Codex subagents answer
    "progress_notes": True,      # False: read only the final answer, skipping the notes in the middle of the work
    "only_chosen_conversations": False,  # True: only the conversations in "chosen_conversations" speak
    "chosen_conversations": [],
    "max_characters": 0,         # 0 = read the whole answer (every 3 thousand characters is one request)
    "lead_silence_ms": 600,      # the speaker takes a moment to wake up and swallowed the first word
    "style": "",                 # "" = the style prompt of the language
    "fallback_voice": "",        # "" = the Microsoft voice of the language
    "offline_voice": "",         # "" = the system voice of the language
}

# Config written before the English rename: old key -> new key. Values equal to the old pt-BR defaults are dropped
# so the fallback voices follow the selected language.
LEGACY_CONFIG_KEYS = {
    "ativo": "enabled", "voz": "voice", "modelo": "model", "velocidade": "speed",
    "subagentes_codex": "codex_subagents", "intermediarias": "progress_notes",
    "so_conversas_escolhidas": "only_chosen_conversations", "conversas_ouvir": "chosen_conversations",
    "max_caracteres": "max_characters", "silencio_inicial_ms": "lead_silence_ms", "estilo": "style",
    "voz_reserva": "fallback_voice", "voz_offline": "offline_voice",
    "atalho_pausa": "pause_shortcut", "atalho_selecao": "selection_shortcut",
}
LEGACY_DEFAULT_VALUES = {"fallback_voice": {"pt-BR-ThalitaMultilingualNeural"},
                         "offline_voice": {"Luciana", "Microsoft Maria Desktop"}}

GEMINI_VOICES = {
    "Kore": ("female", "firm"), "Aoede": ("female", "breezy"), "Leda": ("female", "youthful"),
    "Callirrhoe": ("female", "easy-going"), "Autonoe": ("female", "bright"), "Despina": ("female", "smooth"),
    "Erinome": ("female", "clear"), "Laomedeia": ("female", "upbeat"), "Achernar": ("female", "soft"),
    "Gacrux": ("female", "mature"), "Pulcherrima": ("female", "forward"), "Vindemiatrix": ("female", "gentle"),
    "Sulafat": ("female", "warm"), "Zephyr": ("female", "bright"),
    "Puck": ("male", "upbeat"), "Charon": ("male", "informative"), "Fenrir": ("male", "excitable"),
    "Orus": ("male", "firm"), "Enceladus": ("male", "breathy"), "Iapetus": ("male", "clear"),
    "Umbriel": ("male", "easy-going"), "Algieba": ("male", "smooth"), "Algenib": ("male", "gravelly"),
    "Rasalgethi": ("male", "informative"), "Alnilam": ("male", "firm"), "Schedar": ("male", "even"),
    "Achird": ("male", "friendly"), "Zubenelgenubi": ("male", "casual"), "Sadachbia": ("male", "lively"),
    "Sadaltager": ("male", "knowledgeable"),
}

# Each language: its own name (shown in the menu), its English name (used in the style prompt), the Microsoft
# fallback voice, the offline system voices and a test sentence written in that language.
LANGUAGES = {
    "en-US": {"name": "English (US)", "english": "American English",
              "fallback_voice": "en-US-AvaMultilingualNeural", "mac_voice": "Samantha",
              "windows_voice": "Microsoft Zira Desktop",
              "test": "Done! I deployed the branch and the build passed. The pull request is open on GitHub, "
                      "waiting for review before the merge."},
    "en-GB": {"name": "English (UK)", "english": "British English",
              "fallback_voice": "en-GB-SoniaNeural", "mac_voice": "Daniel",
              "windows_voice": "Microsoft Hazel Desktop",
              "test": "Done! I deployed the branch and the build passed. The pull request is open on GitHub, "
                      "waiting for review before the merge."},
    "pt-BR": {"name": "Português (Brasil)", "english": "Brazilian Portuguese",
              "fallback_voice": "pt-BR-ThalitaMultilingualNeural", "mac_voice": "Luciana",
              "windows_voice": "Microsoft Maria Desktop",
              "test": "Pronto! Fiz o deploy da branch e o build passou. O pull request já está aberto no GitHub, "
                      "esperando review antes do merge."},
    "pt-PT": {"name": "Português (Portugal)", "english": "European Portuguese",
              "fallback_voice": "pt-PT-RaquelNeural", "mac_voice": "Joana",
              "windows_voice": "Microsoft Helia Desktop",
              "test": "Pronto! Fiz o deploy do branch e o build passou. O pull request já está aberto no GitHub, "
                      "à espera de review antes do merge."},
    "es-ES": {"name": "Español (España)", "english": "Castilian Spanish",
              "fallback_voice": "es-ES-XimenaNeural", "mac_voice": "Mónica",
              "windows_voice": "Microsoft Helena Desktop",
              "test": "¡Listo! Hice el deploy de la rama y el build pasó. El pull request ya está abierto en "
                      "GitHub, esperando review antes del merge."},
    "es-MX": {"name": "Español (México)", "english": "Mexican Spanish",
              "fallback_voice": "es-MX-DaliaNeural", "mac_voice": "Paulina",
              "windows_voice": "Microsoft Sabina Desktop",
              "test": "¡Listo! Hice el deploy de la rama y el build pasó. El pull request ya está abierto en "
                      "GitHub, esperando review antes del merge."},
    "fr-FR": {"name": "Français", "english": "French",
              "fallback_voice": "fr-FR-VivienneMultilingualNeural", "mac_voice": "Thomas",
              "windows_voice": "Microsoft Hortense Desktop",
              "test": "C'est fait ! J'ai fait le deploy de la branche et le build est passé. La pull request est "
                      "ouverte sur GitHub, en attente de review avant le merge."},
    "de-DE": {"name": "Deutsch", "english": "German",
              "fallback_voice": "de-DE-SeraphinaMultilingualNeural", "mac_voice": "Anna",
              "windows_voice": "Microsoft Hedda Desktop",
              "test": "Fertig! Ich habe den Branch deployt und der Build ist durchgelaufen. Der Pull Request ist "
                      "auf GitHub offen und wartet vor dem Merge auf ein Review."},
    "it-IT": {"name": "Italiano", "english": "Italian",
              "fallback_voice": "it-IT-ElsaNeural", "mac_voice": "Alice",
              "windows_voice": "Microsoft Elsa Desktop",
              "test": "Fatto! Ho fatto il deploy del branch e la build è passata. La pull request è aperta su "
                      "GitHub, in attesa di review prima del merge."},
    "ja-JP": {"name": "日本語", "english": "Japanese",
              "fallback_voice": "ja-JP-NanamiNeural", "mac_voice": "Kyoko",
              "windows_voice": "Microsoft Haruka Desktop",
              "test": "完了しました！ブランチをデプロイして、ビルドも通りました。プルリクエストは GitHub で開いていて、"
                      "マージ前のレビュー待ちです。"},
    "zh-CN": {"name": "中文（简体）", "english": "Mandarin Chinese",
              "fallback_voice": "zh-CN-XiaoxiaoNeural", "mac_voice": "Tingting",
              "windows_voice": "Microsoft Huihui Desktop",
              "test": "完成了！我已经部署了这个分支，构建也通过了。拉取请求已经在 GitHub 上打开，正在等待合并前的审查。"},
    "ko-KR": {"name": "한국어", "english": "Korean",
              "fallback_voice": "ko-KR-SunHiNeural", "mac_voice": "Yuna",
              "windows_voice": "Microsoft Heami Desktop",
              "test": "완료했어요! 브랜치를 배포했고 빌드도 통과했어요. 풀 리퀘스트는 GitHub에 열려 있고, "
                      "머지 전에 리뷰를 기다리고 있어요."},
}
DEFAULT_LANGUAGE = "en-US"


# ---------------------------------------------------------------- config / log

def migrate_config(stored: dict) -> dict:
    migrated = {}
    for key, value in stored.items():
        new = LEGACY_CONFIG_KEYS.get(key, key)
        if value in LEGACY_DEFAULT_VALUES.get(new, ()) and key != new:
            continue
        if key == new or new not in stored:
            migrated[new] = value
    return migrated


def read_config() -> dict:
    try:
        return {**DEFAULTS, **migrate_config(json.loads(CONFIG.read_text(encoding="utf-8")))}
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(DEFAULTS)


def save_config(cfg: dict) -> None:
    # written next to it and swapped in one go: a reader in the middle (the menu every 1 s, the hooks) never sees
    # an empty file, which turned into DEFAULTS (reading on, every conversation) for a moment
    BASE.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG.with_name(f".config.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, CONFIG)


def log(msg: str) -> None:
    try:
        if LOG.exists() and LOG.stat().st_size > 200_000:
            LOG.write_text(LOG.read_text(encoding="utf-8")[-50_000:], encoding="utf-8")
        with LOG.open("a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- language

def match_language(tags: list[str]) -> str:
    """The first preferred language we support: the exact tag, else the same language in another region."""
    codes = {c.lower(): c for c in LANGUAGES}
    for tag in tags:
        tag = tag.replace("_", "-").lower()
        if tag in codes:
            return codes[tag]
        base = tag.split("-")[0]
        if found := next((c for c in LANGUAGES if c.split("-")[0].lower() == base), None):
            return found
    return DEFAULT_LANGUAGE


def system_language() -> str:
    try:
        return match_language(platform_support.system_languages())
    except Exception as e:
        log(f"system language not read: {e}")
        return DEFAULT_LANGUAGE


def current_language(cfg: dict) -> str:
    chosen = cfg.get("language") or ""
    return chosen if chosen in LANGUAGES else system_language()


def style_prompt(cfg: dict) -> str:
    if cfg.get("style"):
        return cfg["style"]
    language = LANGUAGES[current_language(cfg)]["english"]
    gender = GEMINI_VOICES.get(cfg["voice"], ("female", ""))[0]
    common = (f"Keep the same {gender} voice, steady from the very first word, never starting in a deeper or "
              "different register. Brisk pace, a little faster than normal, with a natural, direct tone, "
              "like a colleague explaining what they just did.")
    if language.endswith("English"):
        return f"Speak in {language}. {common}"
    return (f"Speak in {language}. {common} Pronounce technical terms, tool names and English words with a native "
            f"American English pronunciation, without adapting them to {language}. If the whole text is in "
            "English, read it in American English exactly as written, without translating it and without a "
            "foreign accent.")


def fallback_voice(cfg: dict) -> str:
    return cfg.get("fallback_voice") or LANGUAGES[current_language(cfg)]["fallback_voice"]


def offline_voice(cfg: dict) -> str:
    language = LANGUAGES[current_language(cfg)]
    return cfg.get("offline_voice") or language["windows_voice" if WINDOWS else "mac_voice"]


def test_sentence(cfg: dict | None = None) -> str:
    return LANGUAGES[current_language(cfg or read_config())]["test"]


# ---------------------------------------------------------------- text cleanup

EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D]")


def clean_text(text: str, limit: int = 0) -> str:
    t = re.sub(r"```.*?(```|$)", " ", text, flags=re.S)             # code blocks
    t = re.sub(r"<(oai-mem-citation|citation_entries|rollout_ids|system-reminder)\b[^>]*>.*?(</\1>|$)",
               " ", t, flags=re.S)                                   # Codex memory citations etc.
    t = re.sub(r"<[^>\n]+>", " ", t)                                 # html/xml tags

    def inline(m):
        c = m.group(1).strip()
        if "/" in c and " " not in c:
            last = c.rstrip("/").split("/")[-1]
            if re.search(r"\.\w+(:\d+(-\d+)?)?$", last) or c[0] in "./~":   # path -> just the file
                c = last
            else:                                                    # feature/login -> feature login
                c = c.replace("/", " ")
        return c if len(c) <= 40 else " "
    t = re.sub(r"`([^`\n]+)`", inline, t)

    lines = []
    for line in t.splitlines():
        s = line.strip()
        if re.fullmatch(r"\|?[\s:\-|]+\|?", s) and "-" in s:         # table separator
            continue
        if s.startswith("|"):                                        # table row -> "a, b, c."
            s = ", ".join(c.strip() for c in s.strip("|").split("|") if c.strip())
        s = re.sub(r"^#{1,6}\s*", "", s)                             # headings
        s = re.sub(r"^([-*+]|\d+[.)])\s+", "", s)                    # list markers
        s = re.sub(r"^>\s?", "", s)                                  # quotes
        if s and s[-1] not in ".!?:;,。！？":
            s += "."
        lines.append(s)
    t = " ".join(lines)

    t = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", t)                 # [text](url) -> text
    t = re.sub(r"https?://\S+", " ", t)                              # bare URLs
    t = re.sub(r"(\.\w{1,5}):\d+(-\d+)?\b", r"\1", t)                  # file.ts:42 -> file.ts
    t = re.sub(r"(\*\*|__|\*|~~)", "", t)                            # bold/italic
    t = EMOJI.sub("", t)
    t = t.replace("→", ",").replace("·", ",").replace("—", ",")
    t = re.sub(r"\s+([.,!?;:])", r"\1", t)
    t = re.sub(r"([.,!?;:])\1+", r"\1", t)
    t = re.sub(r"\s+", " ", t).strip(" .,")

    if limit and len(t) > limit:
        cut = max(t.rfind(p, 0, limit) for p in ".!?")
        t = t[: cut + 1 if cut > limit // 2 else limit]
    return t


# ---------------------------------------------------------------- engines (streaming)

find_binary = platform_support.find_binary

FFMPEG = find_binary("ffmpeg")
SEGMENT_MAX = 3000  # characters per Gemini request (the quota is per request, so fewer requests is better)


def key(service: str) -> str | None:
    """Reads the key from the system vault (macOS: login keychain; Windows: Credential Manager)."""
    value = platform_support.read_key(service)
    notice = BASE / f"no-key-{service}"
    if not value:
        if not notice.exists():
            log(f"key {service} not found: using the fallback voice")
        notice.touch()
        return None
    notice.unlink(missing_ok=True)
    return value


def gemini_stream(text: str, voice: str, model: str, style: str):
    """Yields the audio in chunks (PCM 24 kHz, mono, 16 bits) as Gemini generates it."""
    api_key = key("tts-gemini")
    if not api_key:
        raise RuntimeError("no tts-gemini key in the keychain")
    body = {
        "model": model,
        "input": [{"type": "user_input", "content": [{
            "type": "text", "text": text,
            "annotations": [{"type": "speech_metadata", "style": style}],
        }]}],
        "response_format": {"type": "audio"},
        "generation_config": {"speech_config": [{"voice": voice}]},
        "stream": True,
    }
    req = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/interactions",
        data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key})
    try:
        response = urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as e:
        error = f"HTTP {e.code}: {e.read().decode(errors='replace')[:200]}"
        if e.code == 429:
            pause_gemini(error, model)
        raise RuntimeError(error)
    with response:
        for raw in response:
            line = raw.decode(errors="replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            event = json.loads(payload)
            if event.get("error") or event.get("event_type") == "error":
                raise RuntimeError(f"stream error: {str(event)[:200]}")
            delta = event.get("delta") or {}
            if delta.get("type") == "audio":
                yield base64.b64decode(delta["data"])


GEMINI_MODELS = ("gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts")  # separate daily quotas


def pause_file(model: str) -> Path:
    return BASE / f"pause-{model}"


def pause_gemini(error: str, model: str) -> None:
    """Honors Google's 'retry in 10h22m21s'; each model has its own quota."""
    m = re.search(r"retry in ((?:\d+h)?(?:\d+m)?(?:[\d.]+s)?)", error)
    seconds = 0.0
    if m:
        for value, unit in re.findall(r"([\d.]+)([hms])", m.group(1)):
            seconds += float(value) * {"h": 3600, "m": 60, "s": 1}[unit]
    seconds = seconds + 5 if seconds else 120
    pause_file(model).write_text(str(time.time() + seconds), encoding="utf-8")
    log(f"{model} paused for {seconds / 60:.0f} min (usage limit)")


def gemini_paused(model: str | None = None) -> bool:
    """Without a model: True only if every model is paused."""
    def paused(m: str) -> bool:
        try:
            return time.time() < float(pause_file(m).read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return False
    return paused(model) if model else all(paused(m) for m in GEMINI_MODELS)


def paused_until(model: str) -> float | None:
    try:
        until = float(pause_file(model).read_text(encoding="utf-8"))
        return until if until > time.time() else None
    except (FileNotFoundError, ValueError):
        return None


def edge_stream(text: str, voice: str, deliver) -> None:
    """Microsoft fallback voice (free), delivered in MP3 chunks."""
    import asyncio
    import edge_tts

    async def run():
        async for part in edge_tts.Communicate(text, voice, rate="+15%").stream():
            if part["type"] == "audio":
                deliver(("mp3", part["data"]))
    asyncio.run(run())


def split_segments(text: str, maximum: int = SEGMENT_MAX) -> list[str]:
    """Splits the text into parts of up to `maximum` characters, always between sentences."""
    parts, current = [], ""
    # CJK sentence marks come without a space after them; "." without a space is a file name or a number
    for sentence in re.split(r"(?<=[.!?])\s+|(?<=[。！？])", text):
        while len(sentence) > maximum:  # giant sentence: cut at a space
            cut = sentence.rfind(" ", 0, maximum)
            cut = cut if cut > maximum // 2 else maximum
            if current:
                parts.append(current)
                current = ""
            parts.append(sentence[:cut])
            sentence = sentence[cut:].lstrip()
        if current and len(current) + 1 + len(sentence) > maximum:
            parts.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        parts.append(current)
    return parts


def produce(segments: list[str], cfg: dict, deliver) -> str:
    """Generates the audio of each part, in order, delivering chunks (format, bytes).

    Gemini (up to 2 attempts) → Microsoft → system voice. If Gemini fails after it already delivered
    audio for that part, the part isn't repeated in another voice."""
    engines = []
    has_key = bool(key("tts-gemini"))  # without a key every Gemini attempt only delayed the fallback voice
    style = style_prompt(cfg)
    for i, segment in enumerate(segments, 1):
        engine = None
        preferred = cfg["model"]
        for model in [preferred] + [m for m in GEMINI_MODELS if m != preferred]:
            if engine or not has_key or gemini_paused(model):
                continue
            for attempt in (1, 2):
                received = False
                try:
                    for chunk in gemini_stream(segment, cfg["voice"], model, style):
                        received = True
                        deliver(("pcm", chunk))
                    engine = "gemini" if model == preferred else f"gemini ({model})"
                    break
                except Exception as e:
                    log(f"failed {model} part={i} attempt={attempt}: {str(e)[:200]}")
                    if received:
                        engine = "gemini-partial"
                        break
                    if gemini_paused(model) or re.match(r"HTTP 4\d\d", str(e)):
                        break  # request error (or limit): retrying doesn't help
                    time.sleep(0.8)
        if engine is None:
            try:
                edge_stream(segment, fallback_voice(cfg), deliver)
                engine = "edge"
            except Exception as e:
                log(f"failed edge part={i}: {str(e)[:200]}")
                try:
                    deliver(("pcm", platform_support.offline_voice_pcm(segment, offline_voice(cfg))))
                    engine = "offline"
                except Exception as e2:
                    log(f"failed offline voice part={i}: {e2}")
                    engine = "none"
        engines.append(engine)
    return ",".join(engines)


# ---------------------------------------------------------------- queue and playback

class Item:
    """One utterance. The audio is generated in a thread and consumed by the player as it arrives."""

    def __init__(self, text: str, queued_at: float):
        cfg = read_config()
        self.clean = clean_text(text, cfg.get("max_characters", 0))
        self.queued_at = queued_at
        self.chunks: queue.Queue = queue.Queue()
        self.engine = "?"
        if self.clean:
            threading.Thread(target=self._produce, args=(cfg,), daemon=True).start()

    def _produce(self, cfg: dict) -> None:
        try:
            self.engine = produce(split_segments(self.clean), cfg, self.chunks.put)
        except Exception as e:
            log(f"production error: {e}")
        finally:
            self.chunks.put(None)


INITIAL_BUFFER = 24000           # PCM bytes (0.5 s) gathered before starting an utterance with an empty buffer
RATE, BYTES_PER_SEC = 24000, 48000
PAUSED = BASE / "paused"


class Output:
    """The worker's single audio output (PCM 24 kHz, mono). It stays open between utterances, so the
    speaker only needs to wake up once. While paused it plays silence without advancing the audio."""

    def __init__(self):
        self.buffer, self.mutex = bytearray(), threading.Lock()
        self.empty = threading.Event()
        self.empty.set()
        self.paused = False
        self.file = None
        self.stream = None
        threading.Thread(target=self._watch_pause, daemon=True).start()
        if not os.environ.get("TTS_SINK_DIR"):
            import sounddevice as sd
            self.stream = sd.RawOutputStream(samplerate=RATE, channels=1, dtype="int16",
                                             blocksize=1200, callback=self._callback)
            self.stream.start()
            lead = int(read_config().get("lead_silence_ms", 600))
            self.write(bytes(lead * BYTES_PER_SEC // 1000 // 2 * 2))  # wakes the speaker up

    def _watch_pause(self) -> None:
        while True:
            self.paused = PAUSED.exists()
            time.sleep(0.05)

    def _callback(self, out, frames, time_info, status) -> None:
        n = frames * 2
        with self.mutex:
            if self.paused:
                chunk = b""
            else:
                chunk = bytes(self.buffer[:n])
                del self.buffer[:n]
            if not self.buffer:
                self.empty.set()
        out[:len(chunk)] = chunk
        out[len(chunk):] = bytes(n - len(chunk))

    def new_utterance(self) -> None:
        """In the tests, each utterance becomes a separate .wav file."""
        if target := os.environ.get("TTS_SINK_DIR"):
            self.close_file()
            self.file = wave.open(str(Path(target) / f"{time.time_ns()}.wav"), "wb")
            self.file.setnchannels(1)
            self.file.setsampwidth(2)
            self.file.setframerate(RATE)

    def write(self, data: bytes) -> None:
        if self.file:
            self.file.writeframes(data)
            return
        with self.mutex:
            self.buffer += data
            if self.buffer:
                self.empty.clear()

    def is_empty(self) -> bool:
        return self.file is not None or self.empty.is_set()

    def wait_until_empty(self) -> None:
        if self.stream:
            self.empty.wait()
            time.sleep(self.stream.latency + 0.05)  # what was already handed to the speaker

    def close_file(self) -> None:
        if self.file:
            self.file.close()
            self.file = None

    def close(self) -> None:
        self.wait_until_empty()
        self.close_file()
        if self.stream:
            self.stream.stop()
            self.stream.close()


class Converter:
    """Runs the audio through ffmpeg when needed (MP3 from the fallback voice, a file, extra speed)
    and hands ready PCM to the Output."""

    def __init__(self, fmt: str, speed: float, output: Output, file: Path | None = None):
        source = (["-i", str(file)] if file else
                  ["-f", "s16le", "-sample_rate", str(RATE), "-ch_layout", "mono", "-i", "pipe:0"]
                  if fmt == "pcm" else ["-f", "mp3", "-i", "pipe:0"])
        audio_filter = ["-af", f"atempo={speed:.2f}"] if abs(speed - 1.0) > 0.01 else []
        self.format = fmt
        self.proc = subprocess.Popen([FFMPEG, "-loglevel", "quiet", *source, *audio_filter,
                                      "-f", "s16le", "-ar", str(RATE), "-ac", "1", "pipe:1"],
                                     stdin=subprocess.DEVNULL if file else subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **no_window())
        self.reader = threading.Thread(target=self._read, args=(output,), daemon=True)
        self.reader.start()

    def _read(self, output: Output) -> None:
        while data := self.proc.stdout.read(4800):
            output.write(data)

    def write(self, data: bytes) -> None:
        try:
            self.proc.stdin.write(data)
        except BrokenPipeError:
            log("converter closed before the end of the audio")

    def close(self) -> None:
        if self.proc.stdin:
            try:
                self.proc.stdin.close()
            except BrokenPipeError:
                pass
        self.reader.join()
        self.proc.wait()


def save_last(recorded: dict) -> None:
    """Keeps the audio of the last utterance for 'Repeat last' and for trying speeds."""
    if not (recorded["pcm"] or recorded["mp3"]):
        return
    for old in BASE.glob("last.*"):
        old.unlink(missing_ok=True)
    if recorded["pcm"]:
        with wave.open(str(LAST.with_suffix(".wav")), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(bytes(recorded["pcm"]))
    else:
        LAST.with_suffix(".mp3").write_bytes(bytes(recorded["mp3"]))


def play_item(item: Item, output: Output) -> None:
    """Hands an utterance to the output as the audio arrives from the generator."""
    speed = read_config()["speed"]
    direct = abs(speed - 1.0) <= 0.01
    converter, pending, first = None, bytearray(), None
    reserve = INITIAL_BUFFER if output.is_empty() else 0  # empty buffer: gather half a second first
    recorded = {"pcm": bytearray(), "mp3": bytearray()}
    output.new_utterance()
    while (chunk := item.chunks.get()) is not None:
        fmt, data = chunk
        recorded[fmt] += data
        if first is None:
            pending += data
            if fmt == "pcm" and len(pending) < reserve:
                continue
            first = time.time()
            log(f"playing chars={len(item.clean)} first_audio_after={first - item.queued_at:.1f}s")
            data = bytes(pending)
        if fmt == "pcm" and direct:
            if converter:
                converter.close()
                converter = None
            output.write(data)
            continue
        if not converter or converter.format != fmt:
            if converter:
                converter.close()
            converter = Converter(fmt, speed, output)
        converter.write(data)
    if first is None and pending:  # short utterance: it ended before filling the reserve
        log(f"playing chars={len(item.clean)} first_audio_after={time.time() - item.queued_at:.1f}s")
        if direct:
            output.write(bytes(pending))
        else:
            converter = Converter("pcm", speed, output)
            converter.write(bytes(pending))
    if converter:
        converter.close()
    output.wait_until_empty()
    output.close_file()
    log(f"done engine={item.engine} chars={len(item.clean)} total={time.time() - item.queued_at:.1f}s")
    save_last(recorded)


class FileItem:
    """A ready audio file (repeat the last utterance), played through the same queue."""

    def __init__(self, path: Path, queued_at: float):
        self.path, self.queued_at = path, queued_at


def play(item, output: Output) -> None:
    if isinstance(item, FileItem):
        log(f"repeating {item.path.name}")
        output.new_utterance()
        Converter("file", read_config()["speed"], output, file=item.path).close()
        output.wait_until_empty()
        output.close_file()
        item.path.unlink(missing_ok=True)
    else:
        play_item(item, output)


def repeat_last() -> bool:
    """Puts a copy of the last utterance in the queue (it never plays outside of it)."""
    last = next(iter(BASE.glob("last.*")), None)
    if not last:
        return False
    stop()
    QUEUE.mkdir(parents=True, exist_ok=True)
    copy = QUEUE / f"repeat-{time.time_ns()}{last.suffix}"
    shutil.copy(last, copy)
    (QUEUE / f"{time.time_ns()}.ref").write_text(str(copy), encoding="utf-8")
    start_worker()
    return True


def worker_running() -> int | None:
    try:
        pid = int(PIDFILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None
    return pid if "tts.py _worker" in platform_support.process_command(pid) else None


def pause() -> bool:
    """The output stops at the exact point and plays silence; the rest of the utterance keeps being generated."""
    if not worker_running():
        return False  # nothing speaking: don't leave an "armed" pause that would block the next utterance
    PAUSED.touch()
    log("paused")
    return True


def resume() -> None:
    PAUSED.unlink(missing_ok=True)
    log("resuming")


def toggle_pause() -> str:
    if PAUSED.exists():
        resume()
        return "resuming"
    return "paused" if pause() else "nothing playing"


def stop() -> bool:
    """Clears the queue and stops the current utterance (waits for the worker to exit, so the lock isn't lost)."""
    for item in QUEUE.glob("*"):
        item.unlink(missing_ok=True)
    PAUSED.unlink(missing_ok=True)
    pid = worker_running()
    if pid:
        platform_support.kill_tree(pid)
    PIDFILE.unlink(missing_ok=True)
    return bool(pid)


def enqueue(text: str) -> None:
    QUEUE.mkdir(parents=True, exist_ok=True)
    (QUEUE / f"{time.time_ns()}.txt").write_text(text, encoding="utf-8")
    start_worker()


def start_worker() -> None:
    platform_support.start_detached([str(PYTHON), str(Path(__file__).resolve()), "_worker"])


def next_item() -> "Item | FileItem | None":
    for file in sorted(QUEUE.glob("*"), key=lambda a: a.name):
        if file.suffix not in (".txt", ".ref"):
            continue
        try:
            content = file.read_text(encoding="utf-8")
            file.unlink()
        except FileNotFoundError:
            continue
        try:
            queued_at = int(file.stem) / 1e9
        except ValueError:
            queued_at = time.time()
        if file.suffix == ".ref":
            return FileItem(Path(content), queued_at)
        item = Item(content, queued_at)
        if item.clean:
            return item
    return None


def worker() -> None:
    """A single worker at a time drains the queue. A feeder takes each utterance as soon as it arrives and
    starts generating its audio (up to 2 ahead) while the current one plays."""
    lock = FileLock(LOCK)
    if not lock.try_lock():
        return  # another worker is already draining the queue
    PIDFILE.write_text(str(os.getpid()), encoding="utf-8")
    ready: queue.Queue = queue.Queue(maxsize=2)
    mutex, finish = threading.Lock(), threading.Event()

    def feed() -> None:
        while not finish.is_set():
            with mutex:
                item = None if finish.is_set() or ready.full() else next_item()
                if item:
                    ready.put(item)
            if not item:
                time.sleep(0.1)

    threading.Thread(target=feed, daemon=True).start()
    output = None
    try:
        output = Output()
        while True:
            try:
                item = ready.get(timeout=2)
            except queue.Empty:
                with mutex:  # only exits if nothing arrived nor is being prepared
                    if ready.empty() and not any(QUEUE.glob("*.txt")) and not any(QUEUE.glob("*.ref")):
                        finish.set()
                        break
                continue
            play(item, output)
    except Exception as e:
        log(f"worker error: {e}")
    finally:
        finish.set()
        if output:
            # PortAudio's stream.stop() has hung forever in CoreAudio (AudioOutputUnitStop stuck on a mutex): the
            # worker stayed alive and silent, holding the lock, and the queue only grew. Closing is a courtesy
            # (the utterance is over), so it waits at most 3 s and exits anyway.
            closing = threading.Thread(target=output.close, daemon=True)
            closing.start()
            closing.join(3)
            if closing.is_alive():
                log("audio output didn't close in 3 s: forcing the worker to exit")
        PIDFILE.unlink(missing_ok=True)
        lock.release()
        if any(QUEUE.glob("*.txt")) or any(QUEUE.glob("*.ref")):  # something arrived while we were exiting
            start_worker()
        os._exit(0)  # without this, a hung audio thread would keep the process alive


# ---------------------------------------------------------------- hooks

def read_tail(path: str, max_bytes: int = 2_000_000) -> list[dict]:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            raw = f.read().decode(errors="replace")
    except OSError:
        return []
    entries = []
    for line in raw.splitlines():
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def is_narration(block: dict) -> bool:
    """The desktop app saves some progress notes as a "thinking" block marked as narration in the signature.
    Real reasoning comes marked as "thinking"."""
    if block.get("type") != "thinking" or not block.get("thinking", "").strip():
        return False
    try:
        signature = block.get("signature", "")
        return b"narration" in base64.b64decode(signature + "=" * (-len(signature) % 4))[:80]
    except ValueError:
        return False


def claude_turn_texts(path: str) -> tuple[str | None, list[str]]:
    """Id of the current turn (the uuid of the last real user message; None if it is already outside the tail
    read) and the assistant text blocks since then."""
    texts: list[str] = []
    turn = None
    for e in reversed(read_tail(path)):
        content = (e.get("message") or {}).get("content")
        if e.get("type") == "assistant" and isinstance(content, list):
            texts = [c.get("text", "") if c.get("type") == "text" else c.get("thinking", "")
                     for c in content if c.get("type") == "text" or is_narration(c)] + texts
        elif e.get("type") == "user" and not e.get("isMeta"):
            if isinstance(content, str) or (isinstance(content, list) and any(
                    c.get("type") == "text" for c in content)):
                turn = e.get("uuid") or e.get("timestamp")
                break
    return turn, [t for t in texts if t.strip()]


def is_codex_subagent(path: str) -> bool:
    """The Codex session header marks subagents (thread_source = subagent)."""
    try:
        with open(path, encoding="utf-8") as f:
            meta = json.loads(f.readline()).get("payload") or {}
    except (OSError, ValueError):
        return False
    source = meta.get("source")
    return meta.get("thread_source") == "subagent" or (isinstance(source, dict) and "subagent" in source)


def codex_turn_texts(path: str) -> tuple[str | None, list[str]]:
    """Id of the current turn (turn_id of the last task_started; None if it is already outside the tail read)
    and the assistant messages since then."""
    texts: list[str] = []
    turn = None
    for e in reversed(read_tail(path)):
        p = e.get("payload") or {}
        if e.get("type") == "event_msg" and p.get("type") == "task_started":
            turn = p.get("turn_id") or e.get("timestamp")
            break
        if e.get("type") == "response_item" and p.get("type") == "message" \
                and p.get("role") == "assistant" and p.get("phase") != "final_answer":
            texts = [c.get("text", "") for c in p.get("content", [])
                     if c.get("type") == "output_text"] + texts
    return turn, [t for t in texts if t.strip()]


def only_new(session: str, turn: str | None, texts: list[str]) -> list[str]:
    """Filters out what was already spoken in this turn (avoids repeating between PostToolUse and Stop). A new
    turn starts from scratch: the same reply ("Done.") in a later turn is spoken again.
    turn None (turn start outside the tail read) keeps the stored turn: switching turns there would make the
    whole tail be spoken again."""
    STATE.mkdir(parents=True, exist_ok=True)
    for old in STATE.glob("*.json"):
        try:
            if time.time() - old.stat().st_mtime > 3 * 86400:
                old.unlink(missing_ok=True)
        except FileNotFoundError:
            pass  # another hook removed it at the same time
    file = STATE / (re.sub(r"[^\w-]", "_", session) + ".json")
    with FileLock(BASE / "state.lock"):
        try:
            stored = json.loads(file.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            stored = {}
        stored_turn = stored.get("turn") if isinstance(stored, dict) else None
        seen = set(stored.get("seen", [])) if isinstance(stored, dict) else set()
        if turn is not None and turn != stored_turn:
            seen = set()
        new = []
        for t in texts:
            h = hashlib.sha1(" ".join(t.split()).encode()).hexdigest()[:16]
            if h not in seen:
                seen.add(h)
                new.append(t)
        file.write_text(json.dumps({"turn": turn if turn is not None else stored_turn,
                                    "seen": sorted(seen)}), encoding="utf-8")
    return new


# ---------------------------------------------------------------- conversations (which ones may speak)

CONVERSATIONS = BASE / "conversations"
# An "active" conversation: its process still exists and its hook fired recently. Two hours without any event
# (no tool, no end of turn) is a conversation sitting idle waiting for someone: it leaves the list and comes back
# with the next event, with the choice kept in the config. Without a process to check (Codex, Cursor, old
# records), 30 minutes.
CONVERSATION_WINDOW = 2 * 3600
WINDOW_WITHOUT_PROCESS = 30 * 60


def unattended_session() -> bool:
    """A run nobody is watching (claude -p, Agent SDK, monitor agents). Claude Code marks it in each process's
    environment: CLAUDE_CODE_SESSION_ATTENDED=0 in -p, 1 in the desktop app and its subagents, and the value
    isn't inherited from the launcher (a -p started from inside the desktop app also gets 0)."""
    return os.environ.get("CLAUDE_CODE_SESSION_ATTENDED") == "0"


def conversation_file(source: str, session: str) -> Path:
    return CONVERSATIONS / (re.sub(r"[^\w-]", "_", f"{source}-{session}") + ".json")


def claude_title(path: str) -> str:
    """The conversation title in Claude Code: the name given to it, or the user's last request."""
    title = prompt = ""
    for e in read_tail(path, 400_000):
        if e.get("type") == "custom-title":
            title = e.get("customTitle") or title
        elif e.get("type") == "last-prompt":
            prompt = e.get("lastPrompt") or prompt
    return title or prompt


def register_conversation(source: str, session: str, data: dict) -> None:
    """Notes that the conversation exists (even when off or out of the list), so it shows up in the menu."""
    folder = data.get("cwd") or next(iter(data.get("workspace_roots") or []), "")
    file = conversation_file(source, session)
    try:
        CONVERSATIONS.mkdir(parents=True, exist_ok=True)
        for old in CONVERSATIONS.glob("*.json"):
            if time.time() - old.stat().st_mtime > 3 * 86400:
                old.unlink(missing_ok=True)
        try:
            previous = json.loads(file.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            previous = {}
        title = claude_title(data.get("transcript_path") or "") if source == "claude" else ""
        pid = os.environ.get("CLAUDE_PID", "")  # the Claude Code process that owns the conversation
        file.write_text(json.dumps({
            "id": f"{source}:{session}", "source": source, "ts": time.time(),
            "pid": int(pid) if pid.isdigit() else None,
            "folder": Path(folder).name if folder else previous.get("folder", ""),
            "title": (title or previous.get("title", "")).strip()[:80],
        }, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        log(f"conversation not registered: {e}")


def active_conversations() -> list[dict]:
    """Conversations seen in the last hours, the most recent first."""
    found = []
    for file in CONVERSATIONS.glob("*.json"):
        try:
            if time.time() - file.stat().st_mtime > CONVERSATION_WINDOW:
                continue  # "ts" is written with the file: old by date is already out (the menu reads every 1 s)
            c = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        pid = c.get("pid")
        if pid and not platform_support.process_alive(pid):
            continue  # the process ended without SessionEnd (force quit, hang)
        if time.time() - c.get("ts", 0) <= (CONVERSATION_WINDOW if pid else WINDOW_WITHOUT_PROCESS):
            found.append(c)
    return sorted(found, key=lambda c: c["ts"], reverse=True)


def conversation_heard(cfg: dict, id_: str) -> bool:
    return not cfg.get("only_chosen_conversations") or id_ in cfg.get("chosen_conversations", [])


def toggle_conversation(id_: str) -> bool:
    """Adds or removes a conversation from the chosen list; returns whether it is now heard."""
    cfg = read_config()
    chosen = [i for i in cfg.get("chosen_conversations", []) if i != id_]
    added = id_ not in cfg.get("chosen_conversations", [])
    if added:
        chosen.append(id_)
    save_config(dict(cfg, chosen_conversations=chosen))
    return added


def conversation_name(c: dict) -> str:
    name = c.get("title") or c.get("folder") or c["id"].split(":", 1)[1][:8]
    folder = f" · {c['folder']}" if c.get("title") and c.get("folder") else ""
    return f"{name}{folder}  ({c['source'].capitalize()})"


def hook(source: str, event: str) -> None:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        data = {}
    if source in ("codex", "cursor"):
        print("{}")  # Codex requires JSON on stdout; Cursor accepts it
        sys.stdout.flush()
    session = str(data.get("session_id") or data.get("conversation_id") or source)
    if event == "end" or unattended_session():
        # end of the session (SessionEnd), or a run nobody is watching: leaves the list and never speaks
        conversation_file(source, session).unlink(missing_ok=True)
        return
    if source == "codex" and not read_config().get("codex_subagents", False) \
            and is_codex_subagent(data.get("transcript_path") or ""):
        return  # a subagent's answer, not the main conversation
    if source == "cursor" or event == "stop":
        store_last_final(source, data.get("text") or data.get("last_assistant_message") or "")
    cfg = read_config()
    register_conversation(source, session, data)
    if not cfg["enabled"] or PAUSED.exists() or (event == "tool" and not cfg.get("progress_notes", True)):
        return  # off or paused: nothing enters the queue
    transcript = data.get("transcript_path") or ""
    final = ""
    if source == "cursor":
        # each afterAgentResponse is one answer: the generation_id separates one turn from the next
        turn = data.get("generation_id") or None
        notes, final = [], data.get("text") or ""
    else:
        turn, notes = (claude_turn_texts if source == "claude" else codex_turn_texts)(
            transcript) if transcript else (None, [])
        turn = turn or data.get("turn_id") or None
        if event == "stop":
            final = data.get("last_assistant_message") or (notes[-1] if notes else "")
            final_clean = clean_text(final, 10_000)[:200]
            notes = [t for t in notes
                     if not final_clean or not clean_text(t, 10_000).startswith(final_clean[:150])]
    if not cfg.get("progress_notes", True):
        notes = []
    new = only_new(session, turn, [t for t in notes + [final] if t.strip()])
    if not conversation_heard(cfg, f"{source}:{session}"):
        return  # conversation not chosen: what it said counts as seen, so it doesn't all come at once when chosen
    for t in new:
        log(f"hook source={source} event={event} final={t is final} chars={len(t)}")
        enqueue(t)


# ---------------------------------------------------------------- speak now

LAST_ANSWERS = BASE / "last_answers"


def store_last_final(source: str, text: str) -> None:
    """Cache of each tool's last final answer (kept even with reading off)."""
    if text.strip():
        LAST_ANSWERS.mkdir(parents=True, exist_ok=True)
        (LAST_ANSWERS / f"{source}.json").write_text(
            json.dumps({"ts": time.time(), "text": text}, ensure_ascii=False), encoding="utf-8")


def most_recent(*patterns: str) -> Path | None:
    files = [a for p in patterns for a in glob.glob(os.path.expanduser(p))]
    return Path(max(files, key=os.path.getmtime)) if files else None


def to_epoch(iso: str | None, default: float) -> float:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return default


def claude_final() -> tuple[float, str] | None:
    file = most_recent("~/.claude/projects/*/*.jsonl")
    if not file:
        return None
    entries = read_tail(str(file))
    for e in reversed(entries):
        msg = e.get("message") or {}
        blocks = msg.get("content") if isinstance(msg.get("content"), list) else []
        if e.get("type") == "assistant" and msg.get("stop_reason") == "end_turn" \
                and any(b.get("type") == "text" for b in blocks):
            same = [b.get("text", "") for x in entries if x.get("type") == "assistant"
                    and (x.get("message") or {}).get("id") == msg.get("id")
                    for b in (x["message"].get("content") or []) if b.get("type") == "text"]
            return to_epoch(e.get("timestamp"), file.stat().st_mtime), "\n\n".join(same)
    return None


def codex_final() -> tuple[float, str] | None:
    files = sorted(glob.glob(os.path.expanduser("~/.codex/sessions/*/*/*/*.jsonl")),
                   key=os.path.getmtime, reverse=True)
    file = next((Path(a) for a in files[:20] if read_config().get("codex_subagents", False)
                 or not is_codex_subagent(a)), None)
    if not file:
        return None
    for e in reversed(read_tail(str(file))):
        p = e.get("payload") or {}
        ts = to_epoch(e.get("timestamp"), file.stat().st_mtime)
        if e.get("type") == "event_msg" and p.get("type") == "task_complete" \
                and p.get("last_agent_message"):
            return ts, p["last_agent_message"]
        if e.get("type") == "response_item" and p.get("type") == "message" \
                and p.get("role") == "assistant" and p.get("phase") == "final_answer":
            text = "\n\n".join(c.get("text", "") for c in p.get("content", [])
                               if c.get("type") == "output_text")
            if text.strip():
                return ts, text
    return None


def cursor_final() -> tuple[float, str] | None:
    file = most_recent("~/.cursor/projects/*/agent-transcripts/*/*.jsonl",
                       "~/.cursor/projects/*/agent-transcripts/*.jsonl")
    if not file:
        return None
    turn_ended = False
    for e in reversed(read_tail(str(file))):
        if e.get("type") == "turn_ended":
            turn_ended = True
        elif turn_ended and e.get("role") == "assistant":
            texts = [c.get("text", "") for c in (e.get("message") or {}).get("content", [])
                     if c.get("type") == "text"]
            text = "\n\n".join(t.replace("[REDACTED]", "").strip() for t in texts).strip()
            if text:
                return file.stat().st_mtime, text
    return None


def latest_final() -> tuple[float, str, str] | None:
    """The most recent final answer among Claude Code, Codex and Cursor."""
    candidates = []
    for file in LAST_ANSWERS.glob("*.json"):
        try:
            d = json.loads(file.read_text(encoding="utf-8"))
            candidates.append((d["ts"] + 2, file.stem, d["text"]))  # tie: the hook is more faithful
        except (OSError, ValueError, KeyError):
            pass
    for source, find in (("claude", claude_final), ("codex", codex_final), ("cursor", cursor_final)):
        try:
            if r := find():
                candidates.append((r[0], source, r[1]))
        except Exception as e:
            log(f"now: failed reading {source}: {e}")
    candidates = [c for c in candidates if c[2].strip()]
    return max(candidates, key=lambda c: c[0]) if candidates else None


def speak_now() -> str:
    found = latest_final()
    if not found:
        return "No final answer found."
    ts, source, text = found
    stop()
    log(f"now source={source} chars={len(text)}")
    enqueue(text)
    return f"Reading the latest {source.capitalize()} answer ({time.strftime('%H:%M', time.localtime(ts))})."


# ---------------------------------------------------------------- speak selection

def _selection_via_accessibility() -> str:
    """Reads the selection straight from the frontmost app, without touching the clipboard."""
    import ApplicationServices as AS
    from AppKit import NSWorkspace
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if not app:
        return ""
    # asking the frontmost app for its focus works where the "system focus" fails (error -25204)
    element = AS.AXUIElementCreateApplication(app.processIdentifier())
    error, focus = AS.AXUIElementCopyAttributeValue(element, AS.kAXFocusedUIElementAttribute, None)
    if error or not focus:
        return ""
    error, text = AS.AXUIElementCopyAttributeValue(focus, AS.kAXSelectedTextAttribute, None)
    return str(text) if not error and text else ""


def _selection_via_copy() -> str:
    """Fallback for apps that don't expose the selection: ⌘C, read, then restore the clipboard."""
    import Quartz
    from AppKit import NSPasteboard, NSPasteboardItem, NSPasteboardTypeString
    pb = NSPasteboard.generalPasteboard()
    saved = [{t: item.dataForType_(t) for t in item.types()} for item in (pb.pasteboardItems() or [])]
    count = pb.changeCount()
    source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    for pressed in (True, False):
        event = Quartz.CGEventCreateKeyboardEvent(source, 8, pressed)  # the C key
        Quartz.CGEventSetFlags(event, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
    for _ in range(25):
        if pb.changeCount() != count:
            break
        time.sleep(0.02)
    text = pb.stringForType_(NSPasteboardTypeString) if pb.changeCount() != count else ""
    if pb.changeCount() != count:  # puts back what was there before
        pb.clearContents()
        items = []
        for types in saved:
            new = NSPasteboardItem.alloc().init()
            for kind, data in types.items():
                if data is not None:
                    new.setData_forType_(data, kind)
            items.append(new)
        if items:
            pb.writeObjects_(items)
    return str(text or "")


def selected_text() -> str:
    if WINDOWS:
        import win_integration
        return win_integration.selected_text()
    try:
        text = _selection_via_accessibility()
    except Exception as e:
        log(f"selection via accessibility: {e}")
        text = ""
    if text.strip():
        return text
    try:
        return _selection_via_copy()
    except Exception as e:
        log(f"selection via copy: {e}")
        return ""


def speak_selection() -> str:
    """Like macOS Speak Selection: if it is already speaking, stop; otherwise read the selected text."""
    if worker_running():
        stop()
        return "stopped"
    text = selected_text()
    if not text.strip():
        return "nothing selected"
    log(f"selection chars={len(text)}")
    enqueue(text)
    return "reading the selection"


# ---------------------------------------------------------------- key and doctor

def store_key_from_clipboard(name: str, file: str = "") -> str:
    """Stores the copied key (or one read from a file, which is deleted after the import)."""
    service = {"gemini": "tts-gemini", "elevenlabs": "tts-elevenlabs"}.get(name)
    if not service:
        sys.exit("Use: tts key gemini [file]")
    source = Path(file) if file else None
    text = source.read_text(encoding="utf-8-sig") if source else platform_support.read_clipboard()
    value = "".join(text.split())
    if len(value) < 20:
        sys.exit("No key found " + ("in the file." if source else "on the clipboard. Copy the key and run it again."))
    if service == "tts-elevenlabs" and not value.startswith("sk_"):
        sys.exit("This doesn't look like an ElevenLabs key (they start with sk_). Nothing was saved.")
    if service == "tts-gemini" and value.startswith("sk_"):
        sys.exit("This looks like an ElevenLabs key, not a Gemini one. Nothing was saved.")
    platform_support.store_key(service, value)
    if source:
        source.unlink(missing_ok=True)
        return f"{service} saved ({value[:4]}…, {len(value)} characters). Key file deleted."
    platform_support.clear_clipboard()
    return f"{service} saved ({value[:4]}…, {len(value)} characters). Clipboard cleared."


def doctor() -> bool:
    """Checks what tts needs to work on this computer."""
    ok = True

    def line(name: str, good: bool, detail: str = "") -> None:
        nonlocal ok
        ok &= good
        print(f"  {'✅' if good else '❌'} {name}{': ' + detail if detail else ''}")

    print(f"System: {sys.platform} · Python {sys.version.split()[0]}")
    for program in ("ffmpeg", "ffprobe"):
        path = find_binary(program)
        line(program, bool(shutil.which(path) or Path(path).exists()), path)
    try:
        import sounddevice as sd
        line("audio output", True, sd.query_devices(kind="output")["name"])
    except Exception as e:
        line("audio output", False, str(e)[:100])
    try:
        import edge_tts  # noqa: F401
        line("edge-tts (fallback voice)", True)
    except ImportError:
        line("edge-tts (fallback voice)", False, "pip install edge-tts")
    has_key = bool(platform_support.read_key("tts-gemini"))
    line("Gemini key", has_key, "" if has_key else "run: tts key gemini")
    language = current_language(read_config())
    line("voice language", True, f"{language} ({LANGUAGES[language]['name']})")
    if WINDOWS:
        for module in ("keyring", "psutil", "pystray", "PIL"):
            try:
                __import__(module)
                line(module, True)
            except ImportError:
                line(module, False, "pip install")
    settings = {"Claude Code": Path.home() / ".claude/settings.json",
                "Codex": Path.home() / ".codex/hooks.json", "Cursor": Path.home() / ".cursor/hooks.json"}
    for name, path in settings.items():
        if path.exists():
            installed = ".agent-tts" in path.read_text(encoding="utf-8")
            line(f"{name} hooks", installed, "" if installed else "run configure_hooks.py install")
    print("All good." if ok else "Some items need fixing.")
    return ok


# ---------------------------------------------------------------- CLI

def status(cfg: dict) -> None:
    language = current_language(cfg)
    print(f"Reading:    {'on' if cfg['enabled'] else 'OFF'}")
    print(f"Language:   {LANGUAGES[language]['name']} ({language}{'' if cfg.get('language') else ', from the system'})")
    gender, description = GEMINI_VOICES.get(cfg["voice"], ("?", "?"))
    print(f"Voice:      {cfg['voice']} ({gender}, {description})")
    print(f"Speed:      {cfg['speed']:.1f}x  (on top of Gemini's 'fast' pace)")
    print(f"Speaking:   {'yes' if worker_running() else 'no'}  ·  queued: {len(list(QUEUE.glob('*.txt')))}")


def turned_on(args: list[str]) -> bool:
    return args[1] in ("on", "yes", "true", "1")


def main() -> None:
    a = sys.argv[1:]
    cmd = a[0] if a else "status"
    cfg = read_config()

    if cmd == "_worker":
        worker()
    elif cmd == "hook":
        hook(a[1] if len(a) > 1 else "claude", a[2] if len(a) > 2 else "stop")
    elif cmd == "status":
        status(cfg)
    elif cmd in ("on", "off"):
        cfg["enabled"] = cmd == "on"
        save_config(cfg)
        if cmd == "off":
            stop()
        print(f"Reading {'on' if cfg['enabled'] else 'off'}.")
    elif cmd == "language":
        if len(a) > 1:
            code = next((c for c in LANGUAGES if c.lower() == a[1].lower()), None)
            if a[1].lower() in ("system", "auto"):
                code = ""
            elif not code:
                sys.exit(f"Unknown language: {a[1]}. See: tts languages")
            cfg["language"] = code
            save_config(cfg)
        language = current_language(cfg)
        print(f"Language: {LANGUAGES[language]['name']} ({language}{'' if cfg.get('language') else ', from the system'})")
    elif cmd == "languages":
        current = current_language(cfg)
        for code, language in LANGUAGES.items():
            print(f"{'→' if code == current else ' '} {code:<6} {language['name']}")
        print("  system: follow the system language (tts language system)")
    elif cmd == "voice":
        if len(a) == 1:
            status(cfg)
            return
        name = next((v for v in GEMINI_VOICES if v.lower() == a[1].lower()), None)
        if not name:
            sys.exit(f"Unknown voice: {a[1]}. See: tts voices")
        cfg["voice"] = name
        save_config(cfg)
        print(f"Voice: {name} ({', '.join(GEMINI_VOICES[name])})")
    elif cmd == "voices":
        for v, (gender, description) in GEMINI_VOICES.items():
            print(f"{'→' if v == cfg['voice'] else ' '} {v:<14} {gender}, {description}")
    elif cmd in ("speed", "faster", "slower"):
        if cmd == "speed" and len(a) == 1:
            print(f"Speed: {cfg['speed']:.1f}x")
            return
        if cmd == "speed":
            v = float(a[1].replace(",", "."))
        else:
            v = cfg["speed"] + (0.1 if cmd == "faster" else -0.1)
        cfg["speed"] = round(min(2.0, max(1.0, v)), 2)
        save_config(cfg)
        print(f"Speed: {cfg['speed']:.1f}x")
    elif cmd == "subagents":
        if len(a) > 1:
            cfg["codex_subagents"] = turned_on(a)
            save_config(cfg)
        print(f"Codex subagents: {'read' if cfg.get('codex_subagents') else 'ignored'}")
    elif cmd == "progress":
        if len(a) > 1:
            cfg["progress_notes"] = turned_on(a)
            save_config(cfg)
        print(f"Progress notes: {'read' if cfg.get('progress_notes', True) else 'skipped (final answer only)'}")
    elif cmd in ("conversations", "listen"):
        active = active_conversations()
        if cmd == "conversations" and len(a) > 1:
            cfg["only_chosen_conversations"] = turned_on(a)
            save_config(cfg)
        elif cmd == "listen" and len(a) > 1:
            if a[1] == "none":
                save_config(dict(cfg, chosen_conversations=[]))
            elif a[1] == "all":
                save_config(dict(cfg, chosen_conversations=[c["id"] for c in active]))
            else:
                target = next((c for i, c in enumerate(active, 1) if a[1] == str(i) or a[1] == c["id"]), None)
                if not target:
                    sys.exit(f"Unknown conversation: {a[1]}. See: tts conversations")
                if not cfg["only_chosen_conversations"]:
                    save_config(dict(cfg, only_chosen_conversations=True))
                toggle_conversation(target["id"])
        cfg = read_config()
        print("Mode: " + ("only the chosen conversations" if cfg["only_chosen_conversations"]
                          else "every conversation (tts conversations on to choose)"))
        for i, c in enumerate(active, 1):
            print(f"{'🔊' if c['id'] in cfg['chosen_conversations'] else '  '} {i:>2}  {conversation_name(c)}")
        if not active:
            print("(no active conversation right now)")
    elif cmd == "selection":
        print(speak_selection().capitalize() + ".")
    elif cmd == "pause":
        print(toggle_pause().capitalize() + ".")
    elif cmd == "stop":
        print("Stopped." if stop() else "Nothing playing.")
    elif cmd == "repeat":
        if not repeat_last():
            sys.exit("Nothing to repeat yet.")
    elif cmd == "key":
        print(store_key_from_clipboard(a[1] if len(a) > 1 else "", a[2] if len(a) > 2 else ""))
    elif cmd == "doctor":
        sys.exit(0 if doctor() else 1)
    elif cmd == "selftest":
        import selftest
        sys.exit(0 if selftest.main() else 1)
    elif cmd == "now":
        print(speak_now())
    elif cmd == "test":
        enqueue(test_sentence(cfg))
        print(f"Speaking with {cfg['voice']} in {current_language(cfg)} at {cfg['speed']:.1f}x…")
    elif cmd == "say":
        enqueue(" ".join(a[1:]) or sys.stdin.read())
    elif cmd == "log":
        print("\n".join(LOG.read_text(encoding="utf-8").splitlines()[-20:]) if LOG.exists() else "(empty)")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
