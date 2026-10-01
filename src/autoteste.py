#!/usr/bin/env python3
"""Autoteste do áudio. Uso: tts autoteste

1. Integridade (sem som): grava as falas em arquivo e procura silêncios no meio.
2. Ao vivo (com som, ~15 s): falas chegando no meio da reprodução; confere que nunca
   tocam dois áudios ao mesmo tempo e que o intervalo entre falas é curto.
"""
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import tts

FFMPEG = tts.binario("ffmpeg")
FFPROBE = tts.binario("ffprobe")


def esperar_fila(limite: float) -> None:
    t0 = time.time()
    time.sleep(1)
    while (tts.worker_ativo() or any(tts.FILA.glob("*.txt"))) and time.time() - t0 < limite:
        time.sleep(0.3)


def silencios_longos(arquivo: Path) -> int:
    saida = subprocess.run([FFMPEG, "-hide_banner", "-i", str(arquivo), "-af",
                            "silencedetect=noise=-45dB:d=0.9", "-f", "null", "-"],
                           capture_output=True, text=True).stderr
    return len(re.findall(r"silence_end", saida))


def teste_integridade() -> bool:
    print("1. Integridade do áudio (sem som)")
    textos = [
        "Primeira fala curta.",
        "Segunda fala, com deploy, pull request e GitHub no meio da frase.",
        "Terceira fala, longa o bastante para virar duas partes. "
        + "Cada frase desta parte ajuda a passar do limite de um pedido só. " * 28,
    ]
    with tempfile.TemporaryDirectory() as pasta:
        env = dict(os.environ, TTS_SINK_DIR=pasta)
        for texto in textos:
            subprocess.run([str(tts.PYTHON), str(Path(tts.__file__)), "dizer", texto], env=env, check=True)
        esperar_fila(180)
        arquivos = sorted(Path(pasta).glob("*.wav"))
        ok = len(arquivos) == len(textos)
        print(f"   falas gravadas: {len(arquivos)} de {len(textos)}")
        for arquivo in arquivos:
            duracao = float(subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                                            "-of", "csv=p=0", str(arquivo)],
                                           capture_output=True, text=True).stdout or 0)
            buracos = silencios_longos(arquivo)
            ok &= buracos == 0 and duracao > 0.8
            print(f"   {duracao:6.1f}s de áudio, silêncios longos no meio: {buracos}")
    return ok


def teste_ao_vivo() -> bool:
    print("2. Reprodução ao vivo (com som)")
    amostras, rodando = [], True

    def monitorar():
        while rodando:
            pids = subprocess.run(["pgrep", "-f", "ffplay|afplay"], capture_output=True,
                                  text=True).stdout.split()
            amostras.append((time.time(), set(pids)))
            time.sleep(0.05)

    threading.Thread(target=monitorar, daemon=True).start()
    t0 = time.time()
    for atraso, texto in ((0.0, "Autoteste A. Primeira fala."),
                          (1.5, "Autoteste B. Chegou enquanto a primeira tocava."),
                          (3.0, "Autoteste C. Também chegou no meio.")):
        while time.time() - t0 < atraso:
            time.sleep(0.02)
        tts.enfileirar(texto)
    esperar_fila(60)
    rodando = False
    time.sleep(0.1)

    vidas: dict[str, list[float]] = {}
    for t, pids in amostras:
        for pid in pids:
            vidas.setdefault(pid, [t, t])[1] = t
    faixas = sorted(vidas.values())
    simultaneos = max((len(p) for _, p in amostras), default=0)
    intervalos = [faixas[i][0] - faixas[i - 1][1] for i in range(1, len(faixas))]
    print(f"   falas tocadas: {len(faixas)} de 3 · players ao mesmo tempo (máximo): {simultaneos}")
    print(f"   silêncio entre falas: {', '.join(f'{x:.2f}s' for x in intervalos) or '-'}")
    return len(faixas) == 3 and simultaneos == 1 and all(x < 0.6 for x in intervalos)


def main() -> bool:
    cfg = tts.ler_config()
    leitura_antes = cfg["ativo"]
    tts.salvar_config(dict(cfg, ativo=False))  # hooks não entram na fila durante o teste
    tts.parar()
    try:
        integro = teste_integridade()
        ao_vivo = teste_ao_vivo()
    finally:
        tts.salvar_config(dict(tts.ler_config(), ativo=leitura_antes))
    ok = integro and ao_vivo
    print(f"\n{'PASSOU' if ok else 'FALHOU'}: integridade {'ok' if integro else 'falhou'}, "
          f"ao vivo {'ok' if ao_vivo else 'falhou'}")
    return ok


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
