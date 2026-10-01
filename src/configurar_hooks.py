#!/usr/bin/env python3
"""Adiciona (ou remove) os hooks de leitura em voz alta no Claude Code, Codex e Cursor.

Uso: configurar_hooks.py instalar|remover
Mescla com o que já existe: só mexe nas entradas que apontam para ~/.tts-agentes.
"""
import json
import shutil
import sys
from pathlib import Path

HOME = Path.home()
BASE = HOME / ".tts-agentes"
MARCA = ".tts-agentes"


def comando(*args: str) -> str:
    return f"{BASE}/.venv/bin/python {BASE}/tts.py hook " + " ".join(args)


def ler(caminho: Path) -> dict:
    try:
        return json.loads(caminho.read_text())
    except FileNotFoundError:
        return {}


def gravar(caminho: Path, dados: dict) -> None:
    if caminho.exists():
        backup = caminho.with_name(caminho.name + ".bak-tts")
        if not backup.exists():
            shutil.copy(caminho, backup)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(dados, indent=2, ensure_ascii=False) + "\n")


def sem_os_nossos(lista: list) -> list:
    return [h for h in lista if MARCA not in json.dumps(h)]


def configurar(caminho: Path, novos: dict, extra: dict | None = None) -> None:
    dados = ler(caminho)
    if extra:
        for k, v in extra.items():
            dados.setdefault(k, v)
    hooks = dados.setdefault("hooks", {})
    for evento in list(hooks):
        hooks[evento] = sem_os_nossos(hooks[evento])
        if not hooks[evento]:
            del hooks[evento]
    for evento, entrada in novos.items():
        hooks.setdefault(evento, []).append(entrada)
    if not hooks and not novos:
        dados.pop("hooks", None)
    gravar(caminho, dados)
    print(f"  {'atualizado' if novos else 'limpo'}: {caminho}")


def main(acao: str) -> None:
    instalar = acao == "instalar"

    def claude(evento):
        return {"hooks": [{"type": "command", "command": comando("claude", evento), "timeout": 10}]}

    def codex(evento):
        return {"hooks": [{"type": "command", "command": comando("codex", evento), "timeout": 10}]}

    configurar(HOME / ".claude/settings.json", {
        "Stop": claude("stop"),
        "PostToolUse": {"matcher": "*", **claude("tool")},
    } if instalar else {})
    configurar(HOME / ".codex/hooks.json", {
        "Stop": codex("stop"),
        "PostToolUse": codex("tool"),
    } if instalar else {})
    configurar(HOME / ".cursor/hooks.json", {
        "afterAgentResponse": {"command": comando("cursor", "resposta")},
    } if instalar else {}, extra={"version": 1})


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("instalar", "remover"):
        sys.exit(__doc__)
    main(sys.argv[1])
