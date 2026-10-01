#!/usr/bin/env python3
"""Ícone na barra de menus para controlar o tts (voz, velocidade, liga/desliga)."""
import os
import subprocess
import threading
import time
from pathlib import Path

import AppKit
import rumps
from quickmachotkey import mask, quickHotKey
from quickmachotkey.constants import kVK_ANSI_P, optionKey

import tts

ICONE_LIGADO, ICONE_FALANDO, ICONE_DESLIGADO, ICONE_PAUSADO = "🔊", "🗣️", "🔇", "⏸️"
VELOCIDADES = [round(1.0 + i / 10, 1) for i in range(11)]  # 1.0 … 2.0
MODELOS = {
    "gemini-3.8-flash-tts": "Flash (melhor qualidade)",
    "gemini-3.8-flash-lite-tts": "Flash-Lite (mais barato)",
}


def falando() -> bool:
    try:
        os.kill(int(tts.PIDFILE.read_text()), 0)
        return True
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return False


class App(rumps.App):
    def __init__(self):
        super().__init__("TTS Agentes", title=ICONE_LIGADO, quit_button=None)
        cfg = tts.ler_config()

        self.item_ativo = rumps.MenuItem("Leitura ligada", callback=self.alternar)
        self.item_inter = rumps.MenuItem("Ler passos intermediários", callback=self.alternar_inter)
        self.item_sub = rumps.MenuItem("Ler subagentes do Codex", callback=self.alternar_sub)
        self.item_status = rumps.MenuItem("")

        self.menu_voz = rumps.MenuItem("Voz")
        for grupo, filtro in (("Femininas", "feminina"), ("Masculinas", "masculina")):
            self.menu_voz.add(rumps.MenuItem(grupo))
            for nome, desc in tts.VOZES_GEMINI.items():
                if desc.startswith(filtro):
                    item = rumps.MenuItem(f"{nome} — {desc.split(', ')[1]}", callback=self.trocar_voz)
                    item.voz = nome
                    self.menu_voz.add(item)
            self.menu_voz.add(rumps.separator)

        self.menu_vel = rumps.MenuItem("Velocidade")
        for v in VELOCIDADES:
            item = rumps.MenuItem(f"{v:.1f}x" + ("  (rápida base)" if v == 1.0 else ""),
                                  callback=self.trocar_velocidade)
            item.vel = v
            self.menu_vel.add(item)

        self.menu_pausa = rumps.MenuItem("Pausa entre frases")
        for seg in range(4):
            item = rumps.MenuItem(f"{seg} s" + ("  (sem pausa)" if seg == 0 else ""), callback=self.trocar_pausa)
            item.seg = seg
            self.menu_pausa.add(item)

        self.menu_modelo = rumps.MenuItem("Modelo Gemini")
        for mid, desc in MODELOS.items():
            item = rumps.MenuItem(desc, callback=self.trocar_modelo)
            item.modelo = mid
            self.menu_modelo.add(item)

        self.menu = [
            rumps.MenuItem("Pausar / continuar  (⌥P)", callback=self.pausar),
            rumps.MenuItem("Falar agora", callback=self.falar_agora),
            self.item_ativo,
            self.item_inter,
            self.item_sub,
            rumps.MenuItem("Parar fala", callback=self.parar, key="."),
            rumps.MenuItem("Repetir última", callback=self.repetir, key="r"),
            rumps.MenuItem("Testar voz", callback=self.testar, key="t"),
            None,
            self.menu_voz,
            self.menu_vel,
            self.menu_pausa,
            self.menu_modelo,
            None,
            self.item_status,
            rumps.MenuItem("Abrir log", callback=self.abrir_log),
            rumps.MenuItem("Sair", callback=rumps.quit_application),
        ]
        self.atualizar_marcas(cfg)
        self.aviso, self.aviso_ate = "", 0.0
        rumps.Timer(self.tique, 1).start()

    # ------------------------------------------------------------ estado visual

    def atualizar_marcas(self, cfg=None):
        cfg = cfg or tts.ler_config()
        self.item_ativo.state = int(cfg["ativo"])
        self.item_inter.state = int(cfg.get("intermediarias", True))
        self.item_sub.state = int(cfg.get("subagentes_codex", False))
        for item in self.menu_voz.values():
            if hasattr(item, "voz"):
                item.state = int(item.voz == cfg["voz"])
        for item in self.menu_vel.values():
            item.state = int(abs(item.vel - cfg["velocidade"]) < 0.01)
        for item in self.menu_pausa.values():
            item.state = int(item.seg == int(cfg.get("pausa_frases", 0)))
        self.menu_pausa.title = f"Pausa entre frases: {int(cfg.get('pausa_frases', 0))} s"
        for item in self.menu_modelo.values():
            item.state = int(item.modelo == cfg["modelo"])
        self.menu_voz.title = f"Voz: {cfg['voz']}"
        self.menu_vel.title = f"Velocidade: {cfg['velocidade']:.1f}x"

    def tique(self, _):
        cfg = tts.ler_config()
        self.title = (ICONE_PAUSADO if tts.PAUSADO.exists() else ICONE_DESLIGADO if not cfg["ativo"]
                      else ICONE_FALANDO if falando() else ICONE_LIGADO)
        if time.time() < self.aviso_ate:
            self.item_status.title = self.aviso
        elif pausas := [(mod, ate) for mod in tts.MODELOS_GEMINI if (ate := tts.pausa_ate(mod))]:
            nomes = {"gemini-3.8-flash-tts": "Flash", "gemini-3.8-flash-lite-tts": "Flash-Lite"}
            reserva = "Microsoft" if len(pausas) == len(tts.MODELOS_GEMINI) else \
                next(nomes[mod] for mod in tts.MODELOS_GEMINI if mod not in dict(pausas))
            mod, ate = pausas[0]
            self.item_status.title = (f"⚠️ {nomes[mod]} no limite diário até "
                                      f"{time.strftime('%H:%M', time.localtime(ate))}, usando {reserva}")
        else:
            fila = len(list(tts.FILA.glob("*.txt"))) if tts.FILA.exists() else 0
            self.item_status.title = f"Gemini ok · na fila: {fila}"
        self.atualizar_marcas(cfg)

    # ------------------------------------------------------------ ações

    def salvar(self, **mudancas):
        cfg = tts.ler_config()
        cfg.update(mudancas)
        tts.salvar_config(cfg)
        self.atualizar_marcas(cfg)

    def alternar(self, _):
        ativo = not tts.ler_config()["ativo"]
        self.salvar(ativo=ativo)
        if not ativo:
            threading.Thread(target=tts.parar, daemon=True).start()

    def alternar_inter(self, _):
        self.salvar(intermediarias=not tts.ler_config().get("intermediarias", True))

    def alternar_sub(self, _):
        self.salvar(subagentes_codex=not tts.ler_config().get("subagentes_codex", False))

    def trocar_voz(self, item):
        self.salvar(voz=item.voz)

        def apresentar():
            tts.parar()
            tts.enfileirar(f"Oi! Agora quem fala é a voz {item.voz}. O deploy e o build estão prontos.")
        threading.Thread(target=apresentar, daemon=True).start()

    def trocar_velocidade(self, item):
        self.salvar(velocidade=item.vel)
        self.repetir(None)  # ouve na hora, sem gastar API

    def trocar_pausa(self, item):
        self.salvar(pausa_frases=item.seg)

    def trocar_modelo(self, item):
        self.salvar(modelo=item.modelo)

    def parar(self, _):
        threading.Thread(target=tts.parar, daemon=True).start()

    def repetir(self, _):
        threading.Thread(target=tts.repetir_ultimo, daemon=True).start()

    def pausar(self, _):
        threading.Thread(target=tts.alternar_pausa, daemon=True).start()

    def falar_agora(self, _):
        def buscar():
            self.aviso = tts.falar_agora()
            self.aviso_ate = time.time() + 6
        threading.Thread(target=buscar, daemon=True).start()

    def testar(self, _):
        tts.enfileirar(tts.FRASE_TESTE)

    def abrir_log(self, _):
        tts.LOG.touch()
        subprocess.run(["open", "-a", "TextEdit", str(tts.LOG)])


@quickHotKey(virtualKey=kVK_ANSI_P, modifierMask=mask(optionKey))
def atalho_pausa() -> None:
    """⌥P em qualquer app: pausa ou continua a fala."""
    threading.Thread(target=tts.alternar_pausa, daemon=True).start()


if __name__ == "__main__":
    AppKit.NSApplication.sharedApplication().setActivationPolicy_(
        AppKit.NSApplicationActivationPolicyAccessory)  # sem ícone no Dock
    App().run()
