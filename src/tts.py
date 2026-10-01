#!/usr/bin/env python3
"""tts — lê em voz alta as respostas do Claude Code, Codex e Cursor.

Comandos:
  tts                      mostra o status
  tts on | off             liga / desliga a leitura
  tts voz [Nome]           mostra ou troca a voz do Gemini (ex.: tts voz Kore)
  tts vozes                lista as vozes disponíveis
  tts velocidade [x]       mostra ou define a aceleração extra (1.0 a 2.0; 1.0 = "rápida" base)
  tts mais | menos         +0.1 / -0.1 na velocidade
  tts intermediarias on|off   lê ou pula os passos no meio do trabalho
  tts parar                interrompe a fala atual
  tts agora                lê agora a última resposta final (Claude Code, Codex ou Cursor)
  tts repetir              repete a última fala (já com a velocidade atual, sem chamar a API)
  tts testar               fala uma frase de teste
  tts dizer "texto"        fala um texto qualquer
  tts log                  mostra as últimas linhas do log
  tts autoteste            confere integridade, sobreposição e silêncio entre falas

Uso interno (hooks):  tts hook claude|codex stop|tool  ·  tts hook cursor   (JSON do hook no stdin)
"""
import base64
import fcntl
import glob
import hashlib
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import wave
from datetime import datetime
from pathlib import Path

BASE = Path.home() / ".tts-agentes"
CONFIG = BASE / "config.json"
PIDFILE = BASE / "worker.pid"
LOCK = BASE / "worker.lock"
FILA = BASE / "fila"
ESTADO = BASE / "estado"
LOG = BASE / "tts.log"
ULTIMO = BASE / "ultimo"  # ultimo.wav / ultimo.mp3 / ultimo.aiff
PYTHON = BASE / ".venv" / "bin" / "python"

PADRAO = {
    "ativo": True,
    "voz": "Kore",
    "modelo": "gemini-3.8-flash-tts",  # ou gemini-3.8-flash-lite-tts (mais barato)
    "velocidade": 1.0,       # aceleração extra (B) aplicada localmente
    "intermediarias": True,      # False: lê só a resposta final, pulando os passos no meio do trabalho
    "max_caracteres": 3000,
    "silencio_inicial_ms": 600,
    "falar_limite": True,        # no fim da resposta final, fala o % restante do plano (semana ou mês)  # o alto-falante leva um instante para acordar e engolia a 1ª palavra  # respostas maiores são cortadas no fim de uma frase
    "voz_reserva": "pt-BR-ThalitaMultilingualNeural",
    "voz_offline": "Luciana",
}

VOZES_GEMINI = {
    "Kore": "feminina, firme", "Aoede": "feminina, leve", "Leda": "feminina, jovem",
    "Callirrhoe": "feminina, tranquila", "Autonoe": "feminina, clara", "Despina": "feminina, suave",
    "Erinome": "feminina, clara", "Laomedeia": "feminina, animada", "Achernar": "feminina, suave",
    "Gacrux": "feminina, madura", "Pulcherrima": "feminina, direta", "Vindemiatrix": "feminina, gentil",
    "Sulafat": "feminina, calorosa", "Zephyr": "feminina, brilhante",
    "Puck": "masculina, animada", "Charon": "masculina, informativa", "Fenrir": "masculina, empolgada",
    "Orus": "masculina, firme", "Enceladus": "masculina, sussurrada", "Iapetus": "masculina, clara",
    "Umbriel": "masculina, tranquila", "Algieba": "masculina, suave", "Algenib": "masculina, rouca",
    "Rasalgethi": "masculina, informativa", "Alnilam": "masculina, firme", "Schedar": "masculina, uniforme",
    "Achird": "masculina, amigável", "Zubenelgenubi": "masculina, casual", "Sadachbia": "masculina, viva",
    "Sadaltager": "masculina, conhecedora",
}

ESTILO = (
    "Fale em português do Brasil num ritmo ágil e um pouco mais rápido que o normal, "
    "tom natural e direto, como um colega explicando o que acabou de fazer. "
    "Pronuncie termos técnicos, nomes de ferramentas e palavras em inglês com pronúncia "
    "americana nativa, sem aportuguesar."
)

FRASE_TESTE = (
    "Pronto! Fiz o deploy da branch e o build passou. O pull request já está "
    "aberto no GitHub, esperando review antes do merge."
)


# ---------------------------------------------------------------- config / log

def ler_config() -> dict:
    try:
        return {**PADRAO, **json.loads(CONFIG.read_text())}
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(PADRAO)


def salvar_config(cfg: dict) -> None:
    CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")


def log(msg: str) -> None:
    try:
        if LOG.exists() and LOG.stat().st_size > 200_000:
            LOG.write_text(LOG.read_text()[-50_000:])
        with LOG.open("a") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- limpeza do texto

EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D]")


def limpar(texto: str, limite: int) -> str:
    t = re.sub(r"```.*?(```|$)", " ", texto, flags=re.S)           # blocos de código
    t = re.sub(r"<(oai-mem-citation|citation_entries|rollout_ids|system-reminder)\b[^>]*>.*?(</\1>|$)",
               " ", t, flags=re.S)                                   # citações de memória do Codex etc.
    t = re.sub(r"<[^>\n]+>", " ", t)                                 # tags html/xml

    def inline(m):
        c = m.group(1).strip()
        if "/" in c and " " not in c:
            ultimo = c.rstrip("/").split("/")[-1]
            if re.search(r"\.\w+(:\d+(-\d+)?)?$", ultimo) or c[0] in "./~":   # caminho -> só o arquivo
                c = ultimo
            else:                                                    # feature/login -> feature login
                c = c.replace("/", " ")
        return c if len(c) <= 40 else " "
    t = re.sub(r"`([^`\n]+)`", inline, t)

    linhas = []
    for linha in t.splitlines():
        s = linha.strip()
        if re.fullmatch(r"\|?[\s:\-|]+\|?", s) and "-" in s:         # separador de tabela
            continue
        if s.startswith("|"):                                        # linha de tabela -> "a, b, c."
            s = ", ".join(c.strip() for c in s.strip("|").split("|") if c.strip())
        s = re.sub(r"^#{1,6}\s*", "", s)                             # títulos
        s = re.sub(r"^([-*+]|\d+[.)])\s+", "", s)                    # marcadores de lista
        s = re.sub(r"^>\s?", "", s)                                  # citações
        if s and s[-1] not in ".!?:;,":
            s += "."
        linhas.append(s)
    t = " ".join(linhas)

    t = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", t)                 # [texto](url) -> texto
    t = re.sub(r"https?://\S+", " ", t)                              # URLs soltas
    t = re.sub(r"(\.\w{1,5}):\d+(-\d+)?\b", r"\1", t)                  # arquivo.ts:42 -> arquivo.ts
    t = re.sub(r"(\*\*|__|\*|~~)", "", t)                            # negrito/itálico
    t = EMOJI.sub("", t)
    t = t.replace("→", ",").replace("·", ",").replace("—", ",")
    t = re.sub(r"\s+([.,!?;:])", r"\1", t)
    t = re.sub(r"([.,!?;:])\1+", r"\1", t)
    t = re.sub(r"\s+", " ", t).strip(" .,")

    if len(t) > limite:
        corte = max(t.rfind(p, 0, limite) for p in ".!?")
        t = t[: corte + 1 if corte > limite // 2 else limite]
    return t


# ---------------------------------------------------------------- motores (streaming)

def binario(nome: str) -> str:
    """Acha um executável mesmo com o PATH mínimo dos hooks (Homebrew Apple Silicon ou Intel)."""
    for candidato in (shutil.which(nome), f"/opt/homebrew/bin/{nome}", f"/usr/local/bin/{nome}"):
        if candidato and Path(candidato).exists():
            return candidato
    return nome


FFPLAY = binario("ffplay")
FFMPEG = binario("ffmpeg")
SEGMENTO_MAX = 3000  # caracteres por pedido ao Gemini (a cota é por pedido, então menos pedidos é melhor)


def chave(servico: str) -> str | None:
    r = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", servico, "-w"],
                       capture_output=True, text=True)
    return r.stdout.strip() or None


def gemini_stream(texto: str, voz: str, modelo: str, estilo: str = ESTILO):
    """Entrega o áudio em pedaços (PCM 24 kHz, mono, 16 bits) conforme o Gemini vai gerando."""
    key = chave("tts-gemini")
    if not key:
        raise RuntimeError("sem chave tts-gemini no Keychain")
    body = {
        "model": modelo,
        "input": [{"type": "user_input", "content": [{
            "type": "text", "text": texto,
            "annotations": [{"type": "speech_metadata", "style": estilo}],
        }]}],
        "response_format": {"type": "audio"},
        "generation_config": {"speech_config": [{"voice": voz}]},
        "stream": True,
    }
    req = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/interactions",
        data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": key})
    try:
        resposta = urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as e:
        erro = f"HTTP {e.code}: {e.read().decode(errors='replace')[:200]}"
        if e.code == 429:
            pausar_gemini(erro, modelo)
        raise RuntimeError(erro)
    with resposta:
        for bruto in resposta:
            linha = bruto.decode(errors="replace").strip()
            if not linha.startswith("data:"):
                continue
            payload = linha[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            evento = json.loads(payload)
            if evento.get("error") or evento.get("event_type") == "error":
                raise RuntimeError(f"erro no stream: {str(evento)[:200]}")
            delta = evento.get("delta") or {}
            if delta.get("type") == "audio":
                yield base64.b64decode(delta["data"])


MODELOS_GEMINI = ("gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts")  # cotas diárias separadas


def arquivo_pausa(modelo: str) -> Path:
    return BASE / f"pausa-{modelo}"


def pausar_gemini(erro: str, modelo: str) -> None:
    """Respeita o 'retry in 10h22m21s' do Google; cada modelo tem sua própria cota."""
    m = re.search(r"retry in ((?:\d+h)?(?:\d+m)?(?:[\d.]+s)?)", erro)
    segundos = 0.0
    if m:
        for valor, unidade in re.findall(r"([\d.]+)([hms])", m.group(1)):
            segundos += float(valor) * {"h": 3600, "m": 60, "s": 1}[unidade]
    segundos = segundos + 5 if segundos else 120
    arquivo_pausa(modelo).write_text(str(time.time() + segundos))
    log(f"{modelo} pausado por {segundos / 60:.0f} min (limite de uso)")


def gemini_pausado(modelo: str | None = None) -> bool:
    """Sem modelo: True só se todos os modelos estiverem pausados."""
    def pausado(m: str) -> bool:
        try:
            return time.time() < float(arquivo_pausa(m).read_text())
        except (FileNotFoundError, ValueError):
            return False
    return pausado(modelo) if modelo else all(pausado(m) for m in MODELOS_GEMINI)


def pausa_ate(modelo: str) -> float | None:
    try:
        ate = float(arquivo_pausa(modelo).read_text())
        return ate if ate > time.time() else None
    except (FileNotFoundError, ValueError):
        return None


def edge_stream(texto: str, voz: str, entregar) -> None:
    """Voz reserva da Microsoft (grátis), entregue em pedaços de MP3."""
    import asyncio
    import edge_tts

    async def rodar():
        async for parte in edge_tts.Communicate(texto, voz, rate="+15%").stream():
            if parte["type"] == "audio":
                entregar(("mp3", parte["data"]))
    asyncio.run(rodar())


def say_pcm(texto: str, voz: str) -> bytes:
    """Última reserva, offline: voz do macOS no mesmo formato PCM do Gemini."""
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "say.wav"
        subprocess.run(["/usr/bin/say", "-v", voz, "-r", "210", "-o", str(wav),
                        "--data-format=LEI16@24000", texto], check=True)
        with wave.open(str(wav)) as w:
            return w.readframes(w.getnframes())


def segmentar(texto: str, maximo: int = SEGMENTO_MAX) -> list[str]:
    """Divide o texto em partes de até `maximo` caracteres, sempre entre frases."""
    partes, atual = [], ""
    for frase in re.split(r"(?<=[.!?])\s+", texto):
        while len(frase) > maximo:  # frase gigante: corta num espaço
            corte = frase.rfind(" ", 0, maximo)
            corte = corte if corte > maximo // 2 else maximo
            if atual:
                partes.append(atual)
                atual = ""
            partes.append(frase[:corte])
            frase = frase[corte:].lstrip()
        if atual and len(atual) + 1 + len(frase) > maximo:
            partes.append(atual)
            atual = frase
        else:
            atual = f"{atual} {frase}".strip()
    if atual:
        partes.append(atual)
    return partes


def produzir(segmentos: list[str], cfg: dict, entregar) -> str:
    """Gera o áudio de cada parte, em ordem, entregando pedaços (formato, bytes).

    Gemini (até 2 tentativas) → Microsoft → say. Se o Gemini falhar depois de já ter
    entregado áudio daquela parte, não repete a parte em outra voz."""
    motores = []
    for i, segmento in enumerate(segmentos, 1):
        motor = None
        preferido = cfg["modelo"]
        for modelo in [preferido] + [m for m in MODELOS_GEMINI if m != preferido]:
            if motor or gemini_pausado(modelo):
                continue
            for tentativa in (1, 2):
                recebeu = False
                try:
                    for pedaco in gemini_stream(segmento, cfg["voz"], modelo, cfg.get("estilo") or ESTILO):
                        recebeu = True
                        entregar(("pcm", pedaco))
                    motor = "gemini" if modelo == preferido else f"gemini ({modelo})"
                    break
                except Exception as e:
                    log(f"falha {modelo} parte={i} tentativa={tentativa}: {str(e)[:200]}")
                    if recebeu:
                        motor = "gemini-parcial"
                        break
                    if gemini_pausado(modelo) or re.match(r"HTTP 4\d\d", str(e)):
                        break  # erro do pedido (ou limite): repetir não adianta
                    time.sleep(0.8)
        if motor is None:
            try:
                edge_stream(segmento, cfg["voz_reserva"], entregar)
                motor = "edge"
            except Exception as e:
                log(f"falha edge parte={i}: {str(e)[:200]}")
                try:
                    entregar(("pcm", say_pcm(segmento, cfg["voz_offline"])))
                    motor = "say"
                except Exception as e2:
                    log(f"falha say parte={i}: {e2}")
                    motor = "nenhum"
        motores.append(motor)
    return ",".join(motores)


# ---------------------------------------------------------------- fila e reprodução

class Item:
    """Uma fala. O áudio é gerado numa thread e consumido pelo player conforme chega."""

    def __init__(self, texto: str, enfileirado_em: float):
        cfg = ler_config()
        self.limpo = limpar(texto, cfg["max_caracteres"])
        self.enfileirado_em = enfileirado_em
        self.pedacos: queue.Queue = queue.Queue()
        self.motor = "?"
        if self.limpo:
            threading.Thread(target=self._produzir, args=(cfg,), daemon=True).start()

    def _produzir(self, cfg: dict) -> None:
        try:
            self.motor = produzir(segmentar(self.limpo), cfg, self.pedacos.put)
        except Exception as e:
            log(f"erro na produção: {e}")
        finally:
            self.pedacos.put(None)


RESERVA_INICIAL = 24000          # bytes de PCM (0,5 s) acumulados antes de abrir o player
RESPIRO_FINAL = bytes(12000)     # 0,25 s de silêncio para o player não cortar a última sílaba


def filtros(velocidade: float) -> list[str]:
    """Velocidade extra e um silêncio de entrada, para o alto-falante acordar antes da fala."""
    partes = [f"atempo={velocidade:.2f}"] if abs(velocidade - 1.0) > 0.01 else []
    if (atraso := int(ler_config().get("silencio_inicial_ms", 600))) > 0:
        partes.append(f"adelay={atraso}:all=1")
    return ["-af", ",".join(partes)] if partes else []


def abrir_player(formato: str, velocidade: float) -> subprocess.Popen:
    entrada = (["-f", "s16le", "-sample_rate", "24000", "-ch_layout", "mono"]
               if formato == "pcm" else ["-f", "mp3"])
    filtro = filtros(velocidade)
    if destino_teste := os.environ.get("TTS_SINK_DIR"):  # testes: grava em vez de tocar
        saida = Path(destino_teste) / f"{time.time_ns()}.wav"
        comando = [FFMPEG, "-loglevel", "quiet", "-y", *entrada,
                   "-i", "pipe:0", *filtro, str(saida)]
    else:
        comando = [FFPLAY, "-nodisp", "-autoexit", "-loglevel", "quiet",
                   *entrada, "-i", "pipe:0", *filtro]
    return subprocess.Popen(comando, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def fechar_player(player: subprocess.Popen | None) -> None:
    if player:
        try:
            player.stdin.close()
        except BrokenPipeError:
            pass
        player.wait()


def salvar_ultimo(gravado: dict) -> None:
    """Guarda o áudio da última fala para 'Repetir última' e para testar velocidades."""
    if not (gravado["pcm"] or gravado["mp3"]):
        return
    for antigo in BASE.glob("ultimo.*"):
        antigo.unlink(missing_ok=True)
    if gravado["pcm"]:
        with wave.open(str(ULTIMO.with_suffix(".wav")), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(bytes(gravado["pcm"]))
    else:
        ULTIMO.with_suffix(".mp3").write_bytes(bytes(gravado["mp3"]))


def tocar_item(item: Item) -> None:
    """Toca uma fala conforme o áudio chega. Um único player por vez, sempre neste worker."""
    velocidade = ler_config()["velocidade"]
    player, formato_atual, pendente = None, None, bytearray()
    gravado = {"pcm": bytearray(), "mp3": bytearray()}

    def escrever(dados: bytes) -> bool:
        try:
            player.stdin.write(dados)
            return True
        except BrokenPipeError:
            log("player fechou antes do fim do áudio")
            return False

    while (pedaco := item.pedacos.get()) is not None:
        formato, dados = pedaco
        gravado[formato] += dados
        if formato != formato_atual:
            if player is None and pendente:  # troca de voz antes de encher a reserva
                player = abrir_player(formato_atual, velocidade)
                escrever(bytes(pendente))
            if player:
                if formato_atual == "pcm":
                    escrever(RESPIRO_FINAL)
                fechar_player(player)
                player = None
            formato_atual, pendente = formato, bytearray()
        if player is None:
            pendente += dados
            if formato == "pcm" and len(pendente) < RESERVA_INICIAL:
                continue  # meio segundo de reserva evita engasgos no começo
            player = abrir_player(formato, velocidade)
            log(f"tocando chars={len(item.limpo)} primeiro_audio_em="
                f"{time.time() - item.enfileirado_em:.1f}s")
            dados, pendente = bytes(pendente), bytearray()
        if not escrever(dados):
            break
    if player is None and pendente:  # fala curta: o stream acabou antes de encher a reserva
        player = abrir_player(formato_atual, velocidade)
        log(f"tocando chars={len(item.limpo)} primeiro_audio_em={time.time() - item.enfileirado_em:.1f}s")
        escrever(bytes(pendente))
    if player and formato_atual == "pcm":
        escrever(RESPIRO_FINAL)
    fechar_player(player)
    log(f"fim motor={item.motor} chars={len(item.limpo)} total={time.time() - item.enfileirado_em:.1f}s")
    salvar_ultimo(gravado)


class ItemArquivo:
    """Um áudio já pronto (repetir a última fala), tocado pela mesma fila."""

    def __init__(self, caminho: Path, enfileirado_em: float):
        self.caminho, self.enfileirado_em = caminho, enfileirado_em


def tocar(item) -> None:
    if isinstance(item, ItemArquivo):
        log(f"repetindo {item.caminho.name}")
        tocar_arquivo(item.caminho, ler_config()["velocidade"])
        item.caminho.unlink(missing_ok=True)
    else:
        tocar_item(item)


def tocar_arquivo(arquivo: Path, velocidade: float) -> None:
    filtro = filtros(velocidade)
    if destino_teste := os.environ.get("TTS_SINK_DIR"):
        comando = [FFMPEG, "-loglevel", "quiet", "-y", "-i", str(arquivo),
                   *filtro, str(Path(destino_teste) / f"{time.time_ns()}-repetir.wav")]
    else:
        comando = [FFPLAY, "-nodisp", "-autoexit", "-loglevel", "quiet", str(arquivo), *filtro]
    subprocess.run(comando, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def repetir_ultimo() -> bool:
    """Põe uma cópia da última fala na fila (nunca toca por fora dela)."""
    ultimo = next(iter(BASE.glob("ultimo.*")), None)
    if not ultimo:
        return False
    parar()
    FILA.mkdir(parents=True, exist_ok=True)
    copia = FILA / f"repetir-{time.time_ns()}{ultimo.suffix}"
    shutil.copy(ultimo, copia)
    (FILA / f"{time.time_ns()}.ref").write_text(str(copia))
    iniciar_worker()
    return True


def worker_ativo() -> int | None:
    try:
        pid = int(PIDFILE.read_text())
        cmd = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                             capture_output=True, text=True).stdout
        return pid if "tts.py _worker" in cmd else None
    except (FileNotFoundError, ValueError):
        return None


def parar() -> bool:
    """Limpa a fila e interrompe a fala atual (espera o worker sair, para não perder o lock)."""
    for item in FILA.glob("*"):
        item.unlink(missing_ok=True)
    pid = worker_ativo()
    if pid:
        try:
            os.killpg(pid, signal.SIGTERM)
            for _ in range(40):
                os.kill(pid, 0)
                time.sleep(0.05)
        except (ProcessLookupError, PermissionError):
            pass
    PIDFILE.unlink(missing_ok=True)
    return bool(pid)


def enfileirar(texto: str) -> None:
    FILA.mkdir(parents=True, exist_ok=True)
    (FILA / f"{time.time_ns()}.txt").write_text(texto)
    iniciar_worker()


def iniciar_worker() -> None:
    subprocess.Popen([str(PYTHON), str(Path(__file__).resolve()), "_worker"],
                     start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)


def proximo_item() -> "Item | ItemArquivo | None":
    for arquivo in sorted(FILA.glob("*"), key=lambda a: a.name):
        if arquivo.suffix not in (".txt", ".ref"):
            continue
        try:
            conteudo = arquivo.read_text()
            arquivo.unlink()
        except FileNotFoundError:
            continue
        try:
            enfileirado_em = int(arquivo.stem) / 1e9
        except ValueError:
            enfileirado_em = time.time()
        if arquivo.suffix == ".ref":
            return ItemArquivo(Path(conteudo), enfileirado_em)
        item = Item(conteudo, enfileirado_em)
        if item.limpo:
            return item
    return None


def worker() -> None:
    """Um único worker por vez esvazia a fila. Um alimentador pega cada fala assim que ela
    chega e já começa a gerar o áudio (até 2 à frente), enquanto a atual toca."""
    lock = LOCK.open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return  # outro worker já está cuidando da fila
    PIDFILE.write_text(str(os.getpid()))
    prontos: queue.Queue = queue.Queue(maxsize=2)
    trava, encerrar = threading.Lock(), threading.Event()

    def alimentar() -> None:
        while not encerrar.is_set():
            with trava:
                item = None if encerrar.is_set() or prontos.full() else proximo_item()
                if item:
                    prontos.put(item)
            if not item:
                time.sleep(0.1)

    threading.Thread(target=alimentar, daemon=True).start()
    try:
        while True:
            try:
                item = prontos.get(timeout=2)
            except queue.Empty:
                with trava:  # só encerra se nada chegou nem está sendo preparado
                    if prontos.empty() and not any(FILA.glob("*.txt")) and not any(FILA.glob("*.ref")):
                        encerrar.set()
                        break
                continue
            tocar(item)
    except Exception as e:
        log(f"erro no worker: {e}")
    finally:
        encerrar.set()
        PIDFILE.unlink(missing_ok=True)
        fcntl.flock(lock, fcntl.LOCK_UN)
        if any(FILA.glob("*.txt")) or any(FILA.glob("*.ref")):  # chegou algo enquanto saíamos
            iniciar_worker()


# ---------------------------------------------------------------- hooks

def ler_cauda(caminho: str, max_bytes: int = 2_000_000) -> list[dict]:
    try:
        with open(caminho, "rb") as f:
            f.seek(0, 2)
            tamanho = f.tell()
            f.seek(max(0, tamanho - max_bytes))
            bruto = f.read().decode(errors="replace")
    except OSError:
        return []
    entradas = []
    for linha in bruto.splitlines():
        try:
            entradas.append(json.loads(linha))
        except json.JSONDecodeError:
            continue
    return entradas


def e_narracao(bloco: dict) -> bool:
    """O app desktop grava parte das mensagens intermediárias como bloco "thinking" marcado
    como narração na assinatura. Raciocínio de verdade vem marcado como "thinking"."""
    if bloco.get("type") != "thinking" or not bloco.get("thinking", "").strip():
        return False
    try:
        assinatura = bloco.get("signature", "")
        return b"narration" in base64.b64decode(assinatura + "=" * (-len(assinatura) % 4))[:80]
    except ValueError:
        return False


def textos_turno_claude(caminho: str) -> list[str]:
    """Blocos de texto do assistente no turno atual (desde a última mensagem real do usuário)."""
    textos: list[str] = []
    for e in reversed(ler_cauda(caminho)):
        conteudo = (e.get("message") or {}).get("content")
        if e.get("type") == "assistant" and isinstance(conteudo, list):
            textos = [c.get("text", "") if c.get("type") == "text" else c.get("thinking", "")
                      for c in conteudo if c.get("type") == "text" or e_narracao(c)] + textos
        elif e.get("type") == "user" and not e.get("isMeta"):
            if isinstance(conteudo, str) or (isinstance(conteudo, list) and any(
                    c.get("type") == "text" for c in conteudo)):
                break
    return [t for t in textos if t.strip()]


def textos_turno_codex(caminho: str) -> list[str]:
    """Mensagens do assistente no turno atual (desde o último task_started)."""
    textos: list[str] = []
    for e in reversed(ler_cauda(caminho)):
        p = e.get("payload") or {}
        if e.get("type") == "event_msg" and p.get("type") == "task_started":
            break
        if e.get("type") == "response_item" and p.get("type") == "message" \
                and p.get("role") == "assistant" and p.get("phase") != "final_answer":
            textos = [c.get("text", "") for c in p.get("content", [])
                      if c.get("type") == "output_text"] + textos
    return [t for t in textos if t.strip()]


def so_os_novos(sessao: str, textos: list[str]) -> list[str]:
    """Filtra o que já foi falado nesta sessão (evita repetir entre PostToolUse e Stop)."""
    ESTADO.mkdir(parents=True, exist_ok=True)
    for velho in ESTADO.glob("*.json"):
        if time.time() - velho.stat().st_mtime > 3 * 86400:
            velho.unlink(missing_ok=True)
    arq = ESTADO / (re.sub(r"[^\w-]", "_", sessao) + ".json")
    with open(BASE / "estado.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            vistos = set(json.loads(arq.read_text()))
        except (FileNotFoundError, json.JSONDecodeError):
            vistos = set()
        novos = []
        for t in textos:
            h = hashlib.sha1(" ".join(t.split()).encode()).hexdigest()[:16]
            if h not in vistos:
                vistos.add(h)
                novos.append(t)
        arq.write_text(json.dumps(sorted(vistos)))
    return novos


def hook(origem: str, evento: str) -> None:
    try:
        dados = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        dados = {}
    if origem in ("codex", "cursor"):
        print("{}")  # Codex exige JSON no stdout; Cursor aceita
        sys.stdout.flush()
    if origem == "cursor" or evento == "stop":
        guardar_ultima_final(origem, dados.get("text") or dados.get("last_assistant_message") or "")
    cfg = ler_config()
    if not cfg["ativo"] or (evento == "tool" and not cfg.get("intermediarias", True)):
        return
    transcript = dados.get("transcript_path") or ""
    final = ""
    if origem == "cursor":
        intermediarias, final = [], dados.get("text") or ""
    else:
        intermediarias = (textos_turno_claude if origem == "claude" else textos_turno_codex)(
            transcript) if transcript else []
        if evento == "stop":
            final = dados.get("last_assistant_message") or (intermediarias[-1] if intermediarias else "")
            final_limpo = limpar(final, 10_000)[:200]
            intermediarias = [t for t in intermediarias
                              if not final_limpo or not limpar(t, 10_000).startswith(final_limpo[:150])]
    if not cfg.get("intermediarias", True):
        intermediarias = []
    sessao = str(dados.get("session_id") or dados.get("conversation_id") or origem)
    novos = so_os_novos(sessao, [t for t in intermediarias + [final] if t.strip()])
    for t in novos:
        e_final = t is final
        falar = t
        if e_final:
            falar = com_limite(falar, origem, transcript)
        log(f"hook origem={origem} evento={evento} final={e_final} "
            f"chars={len(t)}->{len(falar)}")
        enfileirar(falar)

# ---------------------------------------------------------------- falar agora

ULTIMAS = BASE / "ultimas"


def guardar_ultima_final(origem: str, texto: str) -> None:
    """Cache da última resposta final de cada ferramenta (vale mesmo com a leitura desligada)."""
    if texto.strip():
        ULTIMAS.mkdir(parents=True, exist_ok=True)
        (ULTIMAS / f"{origem}.json").write_text(
            json.dumps({"ts": time.time(), "texto": texto}, ensure_ascii=False))


def mais_recente(*padroes: str) -> Path | None:
    arquivos = [a for p in padroes for a in glob.glob(os.path.expanduser(p))]
    return Path(max(arquivos, key=os.path.getmtime)) if arquivos else None


def para_epoch(iso: str | None, padrao: float) -> float:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return padrao


def final_claude() -> tuple[float, str] | None:
    arq = mais_recente("~/.claude/projects/*/*.jsonl")
    if not arq:
        return None
    entradas = ler_cauda(str(arq))
    for e in reversed(entradas):
        msg = e.get("message") or {}
        blocos = msg.get("content") if isinstance(msg.get("content"), list) else []
        if e.get("type") == "assistant" and msg.get("stop_reason") == "end_turn" \
                and any(b.get("type") == "text" for b in blocos):
            mesma = [b.get("text", "") for x in entradas if x.get("type") == "assistant"
                     and (x.get("message") or {}).get("id") == msg.get("id")
                     for b in (x["message"].get("content") or []) if b.get("type") == "text"]
            return para_epoch(e.get("timestamp"), arq.stat().st_mtime), "\n\n".join(mesma)
    return None


def final_codex() -> tuple[float, str] | None:
    arq = mais_recente("~/.codex/sessions/*/*/*/*.jsonl")
    if not arq:
        return None
    for e in reversed(ler_cauda(str(arq))):
        p = e.get("payload") or {}
        ts = para_epoch(e.get("timestamp"), arq.stat().st_mtime)
        if e.get("type") == "event_msg" and p.get("type") == "task_complete" \
                and p.get("last_agent_message"):
            return ts, p["last_agent_message"]
        if e.get("type") == "response_item" and p.get("type") == "message" \
                and p.get("role") == "assistant" and p.get("phase") == "final_answer":
            texto = "\n\n".join(c.get("text", "") for c in p.get("content", [])
                                if c.get("type") == "output_text")
            if texto.strip():
                return ts, texto
    return None


def final_cursor() -> tuple[float, str] | None:
    arq = mais_recente("~/.cursor/projects/*/agent-transcripts/*/*.jsonl",
                       "~/.cursor/projects/*/agent-transcripts/*.jsonl")
    if not arq:
        return None
    turno_encerrado = False
    for e in reversed(ler_cauda(str(arq))):
        if e.get("type") == "turn_ended":
            turno_encerrado = True
        elif turno_encerrado and e.get("role") == "assistant":
            textos = [c.get("text", "") for c in (e.get("message") or {}).get("content", [])
                      if c.get("type") == "text"]
            texto = "\n\n".join(t.replace("[REDACTED]", "").strip() for t in textos).strip()
            if texto:
                return arq.stat().st_mtime, texto
    return None


def ultima_final() -> tuple[float, str, str] | None:
    """A resposta final mais recente entre Claude Code, Codex e Cursor."""
    candidatos = []
    for arq in ULTIMAS.glob("*.json"):
        try:
            d = json.loads(arq.read_text())
            candidatos.append((d["ts"] + 2, arq.stem, d["texto"]))  # empate: o hook é mais fiel
        except (OSError, ValueError, KeyError):
            pass
    for origem, achar in (("claude", final_claude), ("codex", final_codex), ("cursor", final_cursor)):
        try:
            if r := achar():
                candidatos.append((r[0], origem, r[1]))
        except Exception as e:
            log(f"agora: falha lendo {origem}: {e}")
    candidatos = [c for c in candidatos if c[2].strip()]
    return max(candidatos, key=lambda c: c[0]) if candidatos else None


def falar_agora() -> str:
    achado = ultima_final()
    if not achado:
        return "Não achei nenhuma resposta final."
    ts, origem, texto = achado
    parar()
    falar = com_limite(texto, origem.split()[0])
    log(f"agora origem={origem} chars={len(texto)}->{len(falar)}")
    enfileirar(falar)
    return f"Lendo a última resposta do {origem.capitalize()} ({time.strftime('%H:%M', time.localtime(ts))})."


# ---------------------------------------------------------------- limite do plano

def _restante(usado: float | None, reseta_em: float | None) -> int | None:
    if usado is None:
        return None
    if reseta_em and time.time() > reseta_em:
        return 100  # a janela já renovou desde a última leitura
    return max(0, min(100, round(100 - usado)))


def restante_claude() -> int | None:
    """Semana do plano. Fontes, fica com a mais recente: o histórico de uso que o app desktop
    grava e a barra de status do Claude Code no terminal (tts statusline)."""
    leituras = []
    try:
        d = json.loads((Path.home() / "Library/Application Support/Claude/plan-usage-history.json").read_text())
        amostra = max(d.get("samples", []), key=lambda a: a.get("t", 0))
        if amostra.get("u", {}).get("sd") is not None:
            leituras.append((amostra["t"] / 1000, amostra["u"]["sd"], None))
    except (OSError, ValueError):
        pass
    try:
        d = json.loads((BASE / "limites-claude.json").read_text())
        semana = (d.get("rate_limits") or {}).get("seven_day") or {}
        if semana.get("used_percentage") is not None:
            leituras.append((d["ts"], semana["used_percentage"], semana.get("resets_at")))
    except (OSError, ValueError, KeyError):
        pass
    if not leituras:
        return None
    ts, usado, reseta = max(leituras, key=lambda l: l[0])
    if time.time() - ts > 12 * 3600:
        return None  # velho demais para confiar
    return _restante(usado, reseta)


def restante_codex(transcript: str = "") -> int | None:
    """Semana do plano, gravada pelo próprio Codex nos eventos token_count da sessão."""
    arq = Path(transcript) if transcript and Path(transcript).exists() else \
        mais_recente("~/.codex/sessions/*/*/*/*.jsonl")
    if not arq:
        return None
    for e in reversed(ler_cauda(str(arq))):
        limites = (e.get("payload") or {}).get("rate_limits")
        if not limites:
            continue
        for janela in (limites.get("primary"), limites.get("secondary")):
            if janela and janela.get("window_minutes") == 10080:
                return _restante(janela.get("used_percent"), janela.get("resets_at"))
        return None
    return None


def restante_cursor() -> int | None:
    """Mês do plano, pelo mesmo resumo que o painel do Cursor usa (cache de 5 minutos)."""
    cache = BASE / "limites-cursor.json"
    try:
        d = json.loads(cache.read_text())
        if time.time() - d["ts"] < 300:
            return d["restante"]
    except (OSError, ValueError, KeyError):
        pass
    try:
        import sqlite3
        db = Path.home() / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        token = con.execute("select value from ItemTable where key='cursorAuth/accessToken'").fetchone()[0]
        corpo = token.split(".")[1]
        uid = json.loads(base64.urlsafe_b64decode(corpo + "=" * (-len(corpo) % 4)))["sub"].split("|")[-1]
        req = urllib.request.Request("https://cursor.com/api/usage-summary",
                                     headers={"Cookie": f"WorkosCursorSessionToken={uid}%3A%3A{token}"})
        resumo = json.loads(urllib.request.urlopen(req, timeout=4).read())
        plano = (resumo.get("individualUsage") or {}).get("plan") or {}
        fim = para_epoch(resumo.get("billingCycleEnd"), 0)
        restante = _restante(plano.get("totalPercentUsed"), fim or None)
    except Exception as e:
        log(f"limite cursor: {str(e)[:150]}")
        return None
    cache.write_text(json.dumps({"ts": time.time(), "restante": restante}))
    return restante


def com_limite(texto: str, origem: str, transcript: str = "") -> str:
    """Acrescenta o número no fim da fala (só o número, ex.: 65)."""
    if not ler_config().get("falar_limite", True):
        return texto
    try:
        n = {"claude": restante_claude, "cursor": restante_cursor}.get(origem, lambda: None)() \
            if origem != "codex" else restante_codex(transcript)
    except Exception as e:
        log(f"limite {origem}: {e}")
        n = None
    return f"{texto.rstrip()}\n\n{n}." if n is not None else texto


# ---------------------------------------------------------------- CLI

def status(cfg: dict) -> None:
    print(f"Leitura:    {'ligada' if cfg['ativo'] else 'DESLIGADA'}")
    print(f"Voz:        {cfg['voz']} ({VOZES_GEMINI.get(cfg['voz'], '?')})")
    print(f"Velocidade: {cfg['velocidade']:.1f}x  (sobre o ritmo 'rápido' do Gemini)")
    print(f"Falando:    {'sim' if worker_ativo() else 'não'}  ·  na fila: {len(list(FILA.glob('*.txt')))}")


def main() -> None:
    a = sys.argv[1:]
    cmd = a[0] if a else "status"
    cfg = ler_config()

    if cmd == "_worker":
        worker()
    elif cmd == "hook":
        hook(a[1] if len(a) > 1 else "claude", a[2] if len(a) > 2 else "stop")
    elif cmd == "status":
        status(cfg)
    elif cmd in ("on", "off"):
        cfg["ativo"] = cmd == "on"
        salvar_config(cfg)
        if cmd == "off":
            parar()
        print(f"Leitura {'ligada' if cfg['ativo'] else 'desligada'}.")
    elif cmd == "voz":
        if len(a) == 1:
            status(cfg)
            return
        nome = next((v for v in VOZES_GEMINI if v.lower() == a[1].lower()), None)
        if not nome:
            sys.exit(f"Voz desconhecida: {a[1]}. Veja: tts vozes")
        cfg["voz"] = nome
        salvar_config(cfg)
        print(f"Voz: {nome} ({VOZES_GEMINI[nome]})")
    elif cmd == "vozes":
        for v, d in VOZES_GEMINI.items():
            print(f"{'→' if v == cfg['voz'] else ' '} {v:<14} {d}")
    elif cmd in ("velocidade", "mais", "menos"):
        if cmd == "velocidade" and len(a) == 1:
            print(f"Velocidade: {cfg['velocidade']:.1f}x")
            return
        if cmd == "velocidade":
            v = float(a[1].replace(",", "."))
        else:
            v = cfg["velocidade"] + (0.1 if cmd == "mais" else -0.1)
        cfg["velocidade"] = round(min(2.0, max(1.0, v)), 2)
        salvar_config(cfg)
        print(f"Velocidade: {cfg['velocidade']:.1f}x")
    elif cmd == "intermediarias":
        if len(a) > 1:
            cfg["intermediarias"] = a[1] in ("on", "sim", "ligar")
            salvar_config(cfg)
        print(f"Passos intermediários: {'lidos' if cfg.get('intermediarias', True) else 'pulados (só a resposta final)'}")
    elif cmd == "parar":
        print("Parado." if parar() else "Nada tocando.")
    elif cmd == "repetir":
        if not repetir_ultimo():
            sys.exit("Nada para repetir ainda.")
    elif cmd == "statusline":  # barra de status do Claude Code: guarda os limites do plano
        dados = json.loads(sys.stdin.read() or "{}")
        if dados.get("rate_limits"):
            (BASE / "limites-claude.json").write_text(json.dumps(
                {"ts": time.time(), "rate_limits": dados["rate_limits"]}))
        semana = ((dados.get("rate_limits") or {}).get("seven_day") or {}).get("used_percentage")
        print(f"semana: {100 - round(semana)}% restante" if semana is not None else "")
    elif cmd == "autoteste":
        import autoteste
        sys.exit(0 if autoteste.main() else 1)
    elif cmd == "agora":
        print(falar_agora())
    elif cmd == "testar":
        enfileirar(FRASE_TESTE)
        print(f"Falando com {cfg['voz']} a {cfg['velocidade']:.1f}x…")
    elif cmd == "dizer":
        enfileirar(" ".join(a[1:]) or sys.stdin.read())
    elif cmd == "log":
        print("\n".join(LOG.read_text().splitlines()[-20:]) if LOG.exists() else "(vazio)")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
