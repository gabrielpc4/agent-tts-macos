#!/usr/bin/env python3
"""Ícone na bandeja do Windows para controlar o tts (equivalente ao menubar.py do macOS)."""
import os
import threading
import time

import pystray
from PIL import Image, ImageDraw
from pystray import Menu, MenuItem

import plataforma
import tts
import win_integracao

ATALHOS_PADRAO = {"atalho_pausa": "ctrl+alt+p", "atalho_selecao": "ctrl+alt+s"}
VELOCIDADES = [round(1.0 + i / 10, 1) for i in range(11)]
MODELOS = {
    "gemini-3.8-flash-tts": "Flash (melhor qualidade)",
    "gemini-3.8-flash-lite-tts": "Flash-Lite (mais barato)",
}
CORES = {"ligado": (46, 160, 67), "falando": (31, 111, 235), "pausado": (210, 153, 34), "desligado": (110, 118, 129)}


def cfg() -> dict:
    return tts.ler_config()


def atalho(nome: str) -> str:
    return cfg().get(nome) or ATALHOS_PADRAO[nome]


def falando() -> bool:
    try:
        return plataforma.processo_vivo(int(tts.PIDFILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return False


def estado() -> str:
    if tts.PAUSADO.exists():
        return "pausado"
    if not cfg()["ativo"]:
        return "desligado"
    return "falando" if falando() else "ligado"


def desenhar_icone(nome: str) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, 62, 62), fill=CORES[nome])
    branco = (255, 255, 255, 255)
    if nome == "pausado":
        d.rectangle((20, 18, 27, 46), fill=branco)
        d.rectangle((37, 18, 44, 46), fill=branco)
        return img
    d.polygon([(14, 26), (24, 26), (36, 16), (36, 48), (24, 38), (14, 38)], fill=branco)
    if nome in ("ligado", "falando"):
        d.arc((32, 22, 46, 42), -50, 50, fill=branco, width=4)
        d.arc((36, 16, 54, 48), -50, 50, fill=branco, width=4)
    else:
        d.line((14, 50, 50, 14), fill=branco, width=5)
    return img


def texto_status() -> str:
    pausas = [(m, ate) for m in tts.MODELOS_GEMINI if (ate := tts.pausa_ate(m))]
    if (tts.BASE / "sem-tts-gemini").exists():
        return "Sem chave do Gemini: usando a voz da Microsoft"
    if pausas:
        nomes = {"gemini-3.8-flash-tts": "Flash", "gemini-3.8-flash-lite-tts": "Flash-Lite"}
        mod, ate = pausas[0]
        reserva = "Microsoft" if len(pausas) == len(tts.MODELOS_GEMINI) else \
            next(nomes[m] for m in tts.MODELOS_GEMINI if m not in dict(pausas))
        return f"{nomes[mod]} no limite até {time.strftime('%H:%M', time.localtime(ate))}, usando {reserva}"
    fila = len(list(tts.FILA.glob("*.txt"))) if tts.FILA.exists() else 0
    return f"Gemini ok · na fila: {fila}"


def em_thread(funcao):
    def acao(icone=None, item=None):
        threading.Thread(target=funcao, daemon=True).start()
    return acao


def salvar(**mudancas) -> None:
    c = cfg()
    c.update(mudancas)
    tts.salvar_config(c)


def montar_menu() -> Menu:
    def alternar(chave, padrao):
        def acao(icone, item):
            salvar(**{chave: not cfg().get(chave, padrao)})
            if chave == "ativo" and not cfg()["ativo"]:
                threading.Thread(target=tts.parar, daemon=True).start()
        return acao

    def marcado(chave, padrao):
        return lambda item: bool(cfg().get(chave, padrao))

    # o pystray só aceita funções com exatamente 1 argumento (marcação) ou 2 (ação): sem argumentos padrão
    def igual(chave, valor):
        return lambda item: cfg()[chave] == valor

    def velocidade_igual(valor):
        return lambda item: abs(cfg()["velocidade"] - valor) < 0.01

    def escolher(chave, valor):
        def acao(icone, item):
            salvar(**{chave: valor})
        return acao

    def trocar_voz(nome):
        def acao(icone, item):
            salvar(voz=nome)

            def apresentar():
                tts.parar()
                tts.enfileirar(f"Oi! Agora quem fala é a voz {nome}. O deploy e o build estão prontos.")
            threading.Thread(target=apresentar, daemon=True).start()
        return acao

    vozes = []
    for grupo, filtro in (("Femininas", "feminina"), ("Masculinas", "masculina")):
        itens = [MenuItem(f"{nome} — {desc.split(', ')[1]}", trocar_voz(nome),
                          checked=igual("voz", nome), radio=True)
                 for nome, desc in tts.VOZES_GEMINI.items() if desc.startswith(filtro)]
        vozes.append(MenuItem(grupo, Menu(*itens)))

    def trocar_velocidade(v):
        def acao(icone, item):
            salvar(velocidade=v)
            threading.Thread(target=tts.repetir_ultimo, daemon=True).start()
        return acao

    velocidades = [MenuItem(f"{v:.1f}x" + ("  (rápida base)" if v == 1.0 else ""), trocar_velocidade(v),
                            checked=velocidade_igual(v), radio=True)
                   for v in VELOCIDADES]
    modelos = [MenuItem(desc, escolher("modelo", mid), checked=igual("modelo", mid), radio=True)
               for mid, desc in MODELOS.items()]

    return Menu(
        MenuItem(lambda item: f"Falar seleção  ({atalho('atalho_selecao')})", em_thread(tts.falar_selecao)),
        MenuItem(lambda item: f"Pausar / continuar  ({atalho('atalho_pausa')})", em_thread(tts.alternar_pausa)),
        MenuItem("Falar agora", em_thread(tts.falar_agora)),
        Menu.SEPARATOR,
        MenuItem("Leitura ligada", alternar("ativo", False), checked=marcado("ativo", False)),
        MenuItem("Ler passos intermediários", alternar("intermediarias", True), checked=marcado("intermediarias", True)),
        MenuItem("Ler subagentes do Codex", alternar("subagentes_codex", False), checked=marcado("subagentes_codex", False)),
        MenuItem("Parar fala", em_thread(tts.parar)),
        MenuItem("Repetir última", em_thread(tts.repetir_ultimo)),
        MenuItem("Testar voz", lambda icone, item: tts.enfileirar(tts.FRASE_TESTE)),
        Menu.SEPARATOR,
        MenuItem("Voz", Menu(*vozes)),
        MenuItem("Velocidade", Menu(*velocidades)),
        MenuItem("Modelo Gemini", Menu(*modelos)),
        Menu.SEPARATOR,
        MenuItem(lambda item: texto_status(), None, enabled=False),
        MenuItem("Abrir log", lambda icone, item: (tts.LOG.touch(), os.startfile(str(tts.LOG)))),
        MenuItem("Sair", lambda icone, item: icone.stop()),
    )


def main() -> None:
    instancia = plataforma.TravaArquivo(tts.BASE / "tray.lock")
    tts.BASE.mkdir(exist_ok=True)
    if not instancia.tentar():
        return  # já está aberto
    # ao abrir (login ou reinício), começa desligado e sem pausa pendurada
    tts.salvar_config(dict(cfg(), ativo=False))
    tts.PAUSADO.unlink(missing_ok=True)

    icone = pystray.Icon("tts-agentes", desenhar_icone("desligado"), "TTS Agentes", montar_menu())
    resultado = win_integracao.registrar_atalhos({
        atalho("atalho_pausa"): tts.alternar_pausa,
        atalho("atalho_selecao"): tts.falar_selecao,
    })
    for combinacao, ok in resultado.items():
        if not ok:
            tts.log(f"atalho {combinacao} indisponível (outro programa já usa); mude em config.json")

    def atualizar() -> None:
        # Só reconstrói o menu quando algo mudou: refazê-lo a cada segundo fecharia o menu aberto
        ultima = None
        while True:
            nome = estado()
            c = cfg()
            assinatura = (nome, texto_status(), c["voz"], c["velocidade"], c["modelo"], c["ativo"],
                          c.get("intermediarias", True), c.get("subagentes_codex", False))
            if assinatura != ultima:
                icone.icon = desenhar_icone(nome)
                icone.title = f"TTS Agentes: {nome}"
                icone.update_menu()
                ultima = assinatura
            time.sleep(1)

    threading.Thread(target=atualizar, daemon=True).start()
    icone.run()
    instancia.soltar()


if __name__ == "__main__":
    main()
