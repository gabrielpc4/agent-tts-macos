"""Tudo o que muda entre macOS e Windows fica aqui, para o resto do código ser igual nos dois."""
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

SERVICO_KEYRING = "tts-agentes"
CRIAR_SEM_JANELA = 0x08000000
CRIAR_GRUPO_NOVO = 0x00000200
CRIAR_FORA_DO_JOB = 0x01000000


def preparar_console() -> None:
    """No Windows o console e os pipes usam a página de código local; o hook recebe JSON em UTF-8."""
    if not WINDOWS:
        return
    for fluxo in (sys.stdin, sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass  # sem console (pythonw) o fluxo é None


def sem_janela() -> dict:
    """Argumentos extras de subprocess para não piscar janelas de console no Windows."""
    return {"creationflags": CRIAR_SEM_JANELA} if WINDOWS else {}


def binario(nome: str) -> str:
    """Acha um executável mesmo com o PATH mínimo dos hooks."""
    candidatos = [shutil.which(nome)]
    if WINDOWS:
        local = os.environ.get("LOCALAPPDATA", "")
        candidatos += [
            str(Path(local) / "Microsoft" / "WinGet" / "Links" / f"{nome}.exe"),
            str(Path(os.environ.get("ProgramData", "C:/ProgramData")) / "chocolatey" / "bin" / f"{nome}.exe"),
            str(Path.home() / "scoop" / "shims" / f"{nome}.exe"),
        ]
    else:
        candidatos += [f"/opt/homebrew/bin/{nome}", f"/usr/local/bin/{nome}"]
    for candidato in candidatos:
        if candidato and Path(candidato).exists():
            return candidato
    return nome


def python_do_venv(base: Path) -> Path:
    return base / ".venv" / ("Scripts/python.exe" if WINDOWS else "bin/python")


# ---------------------------------------------------------------- chaves

def ler_chave(servico: str) -> str | None:
    """macOS: só o chaveiro de login (procurar em todos faz o sistema pedir senhas de chaveiros
    trancados). Windows: Gerenciador de Credenciais."""
    if WINDOWS:
        try:
            import keyring
            return keyring.get_password(SERVICO_KEYRING, servico) or None
        except Exception:
            return None
    login = Path.home() / "Library/Keychains/login.keychain-db"
    r = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", servico, "-w", str(login)],
                       capture_output=True, text=True)
    return r.stdout.strip() or None


def guardar_chave(servico: str, valor: str) -> None:
    if WINDOWS:
        import keyring
        keyring.set_password(SERVICO_KEYRING, servico, valor)
        return
    login = Path.home() / "Library/Keychains/login.keychain-db"
    subprocess.run(["/usr/bin/security", "add-generic-password", "-U", "-a", os.environ.get("USER", ""),
                    "-s", servico, "-w", valor, str(login)], check=True)


# ---------------------------------------------------------------- área de transferência (para guardar chave)

def ler_area_de_transferencia() -> str:
    if WINDOWS:
        import win_integracao
        return win_integracao.ler_clipboard() or ""
    return subprocess.run(["pbpaste"], capture_output=True, text=True).stdout


def limpar_area_de_transferencia() -> None:
    if WINDOWS:
        import win_integracao
        win_integracao.limpar_clipboard()
    else:
        subprocess.run(["pbcopy"], input="", text=True)


# ---------------------------------------------------------------- trava de arquivo

class TravaArquivo:
    """Trava exclusiva entre processos; o sistema solta sozinho se o processo morrer."""

    def __init__(self, caminho: Path):
        self.caminho = Path(caminho)
        self.arq = None

    def _abrir(self) -> None:
        if self.arq is None:
            self.arq = open(self.caminho, "a+b")

    def tentar(self) -> bool:
        self._abrir()
        try:
            if WINDOWS:
                import msvcrt
                self.arq.seek(0)
                msvcrt.locking(self.arq.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.arq, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def travar(self, espera: float = 5.0) -> bool:
        fim = time.time() + espera
        while not self.tentar():
            if time.time() > fim:
                return False
            time.sleep(0.02)
        return True

    def soltar(self) -> None:
        if self.arq is None:
            return
        try:
            if WINDOWS:
                import msvcrt
                self.arq.seek(0)
                msvcrt.locking(self.arq.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.arq, fcntl.LOCK_UN)
        except OSError:
            pass
        self.arq.close()
        self.arq = None

    def __enter__(self):
        self.travar()
        return self

    def __exit__(self, *a):
        self.soltar()


# ---------------------------------------------------------------- processos

def comando_do_processo(pid: int) -> str:
    """Linha de comando do processo, ou '' se ele não existe."""
    if WINDOWS:
        try:
            import psutil
            return " ".join(psutil.Process(pid).cmdline())
        except Exception:
            return ""
    return subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                          capture_output=True, text=True).stdout


def processo_vivo(pid: int) -> bool:
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


def encerrar_arvore(pid: int) -> None:
    """Encerra o worker e os processos que ele criou (ffmpeg etc.) e espera ele sair."""
    if WINDOWS:
        try:
            import psutil
            pai = psutil.Process(pid)
            filhos = pai.children(recursive=True)
            for p in filhos + [pai]:
                try:
                    p.terminate()
                except psutil.Error:
                    pass
            psutil.wait_procs(filhos + [pai], timeout=2)
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


def iniciar_destacado(args: list[str]) -> None:
    """Roda um processo que continua vivo depois que quem o iniciou (o hook) termina."""
    comum = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    if not WINDOWS:
        subprocess.Popen(args, start_new_session=True, **comum)
        return
    flags = CRIAR_SEM_JANELA | CRIAR_GRUPO_NOVO
    try:
        subprocess.Popen(args, creationflags=flags | CRIAR_FORA_DO_JOB, **comum)
    except OSError:  # o job do chamador não permite sair dele
        subprocess.Popen(args, creationflags=flags, **comum)


# ---------------------------------------------------------------- voz offline (última reserva)

_SCRIPT_SAPI = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
if ($env:TTS_VOZ) { try { $s.SelectVoice($env:TTS_VOZ) } catch {} }
$s.Rate = 2
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(24000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$s.SetOutputToWaveFile($env:TTS_WAV, $fmt)
$s.Speak([System.IO.File]::ReadAllText($env:TTS_TXT, [System.Text.Encoding]::UTF8))
$s.Dispose()
"""


def voz_offline_pcm(texto: str, voz: str) -> bytes:
    """PCM 24 kHz, mono, 16 bits, com a voz do sistema: macOS `say`, Windows SAPI."""
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "voz.wav"
        if WINDOWS:
            txt = Path(td) / "texto.txt"
            txt.write_text(texto, encoding="utf-8")
            ambiente = dict(os.environ, TTS_VOZ=voz, TTS_WAV=str(wav), TTS_TXT=str(txt))
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _SCRIPT_SAPI],
                           env=ambiente, check=True, **sem_janela())
        else:
            subprocess.run(["/usr/bin/say", "-v", voz, "-r", "210", "-o", str(wav),
                            "--data-format=LEI16@24000", texto], check=True)
        with wave.open(str(wav)) as w:
            return w.readframes(w.getnframes())


def voz_offline_padrao() -> str:
    return "Microsoft Maria Desktop" if WINDOWS else "Luciana"
