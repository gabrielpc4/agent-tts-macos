"""Everything that differs between macOS and Windows lives here, so the rest of the code is the same on both."""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

WINDOWS = sys.platform == "win32"
MAC = sys.platform == "darwin"

KEYRING_SERVICE = "agent-tts"
LEGACY_KEYRING_SERVICE = "tts-agentes"  # Windows Credential Manager service used before the English rename
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def prepare_console() -> None:
    """On Windows the console and pipes use the local code page; the hook receives JSON in UTF-8."""
    if not WINDOWS:
        return
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass  # without a console (pythonw) the stream is None


def no_window() -> dict:
    """Extra subprocess arguments so no console window flashes on Windows."""
    return {"creationflags": CREATE_NO_WINDOW} if WINDOWS else {}


def find_binary(name: str) -> str:
    """Finds an executable even with the minimal PATH hooks run with."""
    candidates = [shutil.which(name)]
    if WINDOWS:
        local = os.environ.get("LOCALAPPDATA", "")
        candidates += [
            str(Path(local) / "Microsoft" / "WinGet" / "Links" / f"{name}.exe"),
            str(Path(os.environ.get("ProgramData", "C:/ProgramData")) / "chocolatey" / "bin" / f"{name}.exe"),
            str(Path.home() / "scoop" / "shims" / f"{name}.exe"),
        ]
    else:
        candidates += [f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}"]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return name


def venv_python(base: Path) -> Path:
    return base / ".venv" / ("Scripts/python.exe" if WINDOWS else "bin/python")


# ---------------------------------------------------------------- keys

def read_key(service: str) -> str | None:
    """macOS: only the login keychain (searching all of them makes the system ask for the passwords of
    locked keychains). Windows: Credential Manager."""
    if WINDOWS:
        try:
            import keyring
            return (keyring.get_password(KEYRING_SERVICE, service)
                    or keyring.get_password(LEGACY_KEYRING_SERVICE, service) or None)
        except Exception:
            return None
    login = Path.home() / "Library/Keychains/login.keychain-db"
    r = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", service, "-w", str(login)],
                       capture_output=True, text=True)
    return r.stdout.strip() or None


def store_key(service: str, value: str) -> None:
    if WINDOWS:
        import keyring
        keyring.set_password(KEYRING_SERVICE, service, value)
        return
    login = Path.home() / "Library/Keychains/login.keychain-db"
    subprocess.run(["/usr/bin/security", "add-generic-password", "-U", "-a", os.environ.get("USER", ""),
                    "-s", service, "-w", value, str(login)], check=True)


# ---------------------------------------------------------------- clipboard (for storing the key)

def read_clipboard() -> str:
    if WINDOWS:
        import win_integration
        return win_integration.read_clipboard() or ""
    return subprocess.run(["pbpaste"], capture_output=True, text=True).stdout


def clear_clipboard() -> None:
    if WINDOWS:
        import win_integration
        win_integration.clear_clipboard()
    else:
        subprocess.run(["pbcopy"], input="", text=True)


# ---------------------------------------------------------------- system language

def system_languages() -> list[str]:
    """The user's preferred languages, most preferred first, as BCP 47 tags ("pt-BR", "en-US")."""
    tags: list[str] = []
    if MAC:
        try:
            from Foundation import NSLocale
            tags = [str(t) for t in NSLocale.preferredLanguages()]
        except Exception:
            out = subprocess.run(["/usr/bin/defaults", "read", "-g", "AppleLanguages"],
                                 capture_output=True, text=True).stdout
            tags = [t.strip(' \t\n",()') for t in out.split(",")]
    elif WINDOWS:
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            count, size = ctypes.c_ulong(), ctypes.c_ulong()
            if kernel32.GetUserPreferredUILanguages(8, ctypes.byref(count), None, ctypes.byref(size)):
                buffer = ctypes.create_unicode_buffer(size.value)
                if kernel32.GetUserPreferredUILanguages(8, ctypes.byref(count), buffer, ctypes.byref(size)):
                    tags = [t for t in ctypes.wstring_at(buffer, size.value).split("\0") if t]
        except Exception:
            pass
    if not tags:
        import locale
        tags = [locale.getlocale()[0] or os.environ.get("LANG", "")]
    return [t.replace("_", "-").split(".")[0] for t in tags if t]


# ---------------------------------------------------------------- file lock

class FileLock:
    """Exclusive lock between processes; the system releases it if the process dies."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.file = None

    def _open(self) -> None:
        if self.file is None:
            self.file = open(self.path, "a+b")

    def try_lock(self) -> bool:
        self._open()
        try:
            if WINDOWS:
                import msvcrt
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def lock(self, timeout: float = 5.0) -> bool:
        end = time.time() + timeout
        while not self.try_lock():
            if time.time() > end:
                return False
            time.sleep(0.02)
        return True

    def release(self) -> None:
        if self.file is None:
            return
        try:
            if WINDOWS:
                import msvcrt
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_UN)
        except OSError:
            pass
        self.file.close()
        self.file = None

    def __enter__(self):
        self.lock()
        return self

    def __exit__(self, *a):
        self.release()


# ---------------------------------------------------------------- processes

def process_command(pid: int) -> str:
    """The process command line, or '' if it doesn't exist."""
    if WINDOWS:
        try:
            import psutil
            return " ".join(psutil.Process(pid).cmdline())
        except Exception:
            return ""
    return subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                          capture_output=True, text=True).stdout


def process_alive(pid: int) -> bool:
    if WINDOWS:
        try:
            import psutil
            return psutil.pid_exists(pid)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def kill_tree(pid: int) -> None:
    """Ends the worker and the processes it started (ffmpeg etc.) and waits for it to exit."""
    if WINDOWS:
        try:
            import psutil
            parent = psutil.Process(pid)
            children = parent.children(recursive=True)
            for p in children + [parent]:
                try:
                    p.terminate()
                except psutil.Error:
                    pass
            psutil.wait_procs(children + [parent], timeout=2)
        except Exception:
            pass
        return
    import signal
    try:
        os.killpg(pid, signal.SIGTERM)
        for _ in range(40):
            os.kill(pid, 0)
            time.sleep(0.05)
    except (ProcessLookupError, PermissionError):
        pass


def start_detached(args: list[str]) -> None:
    """Runs a process that stays alive after its caller (the hook) exits."""
    common = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    if not WINDOWS:
        subprocess.Popen(args, start_new_session=True, **common)
        return
    flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(args, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, **common)
    except OSError:  # the caller's job doesn't allow breaking away
        subprocess.Popen(args, creationflags=flags, **common)


# ---------------------------------------------------------------- offline voice (last fallback)

_SAPI_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
if ($env:TTS_VOICE) { try { $s.SelectVoice($env:TTS_VOICE) } catch {} }
$s.Rate = 2
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(24000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$s.SetOutputToWaveFile($env:TTS_WAV, $fmt)
$s.Speak([System.IO.File]::ReadAllText($env:TTS_TXT, [System.Text.Encoding]::UTF8))
$s.Dispose()
"""


def offline_voice_pcm(text: str, voice: str) -> bytes:
    """PCM 24 kHz, mono, 16 bits, with the system voice: macOS `say`, Windows SAPI."""
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "voice.wav"
        if WINDOWS:
            txt = Path(td) / "text.txt"
            txt.write_text(text, encoding="utf-8")
            env = dict(os.environ, TTS_VOICE=voice, TTS_WAV=str(wav), TTS_TXT=str(txt))
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _SAPI_SCRIPT],
                           env=env, check=True, **no_window())
        else:
            command = ["/usr/bin/say", "-r", "210", "-o", str(wav), "--data-format=LEI16@24000", text]
            if subprocess.run(command[:1] + ["-v", voice] + command[1:]).returncode:
                subprocess.run(command, check=True)  # voice not installed: the system default voice
        with wave.open(str(wav)) as w:
            return w.readframes(w.getnframes())
