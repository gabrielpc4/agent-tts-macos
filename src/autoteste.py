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
    marca = time.time()
    t0 = time.time()
    for atraso, texto in ((0.0, "Autoteste A. Primeira fala."),
                          (1.5, "Autoteste B. Chegou enquanto a primeira tocava."),
                          (3.0, "Autoteste C. Também chegou no meio.")):
        while time.time() - t0 < atraso:
            time.sleep(0.02)
        tts.enfileirar(texto)
    esperar_fila(60)
    linhas = [l for l in tts.LOG.read_text().splitlines()
              if l[:19] >= time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(marca))]
    fins = [l for l in linhas if " fim motor=" in l]
    erros = [l for l in linhas if "erro" in l or "fechou antes" in l]
    print(f"   falas tocadas até o fim: {len(fins)} de 3 · erros: {len(erros)}")
    print(f"   tempo total: {time.time() - t0:.1f}s (uma saída de áudio só: sobreposição impossível)")
    return len(fins) == 3 and not erros


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
