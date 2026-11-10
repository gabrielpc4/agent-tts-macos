#!/usr/bin/env python3
"""Adds (or removes) the read-aloud hooks in Claude Code, Codex and Cursor.

Usage: configure_hooks.py install|remove
Merges with what is already there: only touches the entries that point to this app's folder (and to the
folder used before the English rename, ~/.tts-agentes).
"""
import json
import shutil
import sys
from pathlib import Path

HOME = Path.home()
BASE = HOME / ".agent-tts"
MARKS = (".agent-tts", ".tts-agentes")


def command(*args: str) -> str:
    if sys.platform == "win32":
        # Claude Code runs hooks in Git for Windows' bash: forward slashes and quoted paths
        python = (BASE / ".venv" / "Scripts" / "python.exe").as_posix()
        return f'"{python}" "{(BASE / "tts.py").as_posix()}" hook ' + " ".join(args)
    return f"{BASE}/.venv/bin/python {BASE}/tts.py hook " + " ".join(args)


def read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def write(path: Path, data: dict) -> None:
    if path.exists():
        backup = path.with_name(path.name + ".bak-tts")
        if not backup.exists():
            shutil.copy(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def without_ours(entries: list) -> list:
    return [h for h in entries if not any(mark in json.dumps(h) for mark in MARKS)]


def configure(path: Path, new: dict, extra: dict | None = None) -> None:
    data = read(path)
    if extra:
        for k, v in extra.items():
            data.setdefault(k, v)
    hooks = data.setdefault("hooks", {})
    for event in list(hooks):
        hooks[event] = without_ours(hooks[event])
        if not hooks[event]:
            del hooks[event]
    for event, entry in new.items():
        hooks.setdefault(event, []).append(entry)
    if not hooks and not new:
        data.pop("hooks", None)
    write(path, data)
    print(f"  {'updated' if new else 'cleaned'}: {path}")


def main(action: str) -> None:
    install = action == "install"

    def claude(event):
        return {"hooks": [{"type": "command", "command": command("claude", event), "timeout": 10}]}

    def codex(event):
        return {"hooks": [{"type": "command", "command": command("codex", event), "timeout": 10}]}

    configure(HOME / ".claude/settings.json", {
        "Stop": claude("stop"),
        # PreToolUse speaks the note written before a tool as soon as the tool starts; PostToolUse only fires when
        # it ends (minutes for a long command) and stays as a catch-up for text flushed late to the transcript.
        "PreToolUse": {"matcher": "*", **claude("tool")},
        "PostToolUse": {"matcher": "*", **claude("tool")},
        "SessionEnd": claude("end"),  # removes the conversation from the menu list
    } if install else {})
    # Codex and Cursor are only configured if they are installed (their config folder exists)
    if (HOME / ".codex").exists():
        configure(HOME / ".codex/hooks.json", {
            "Stop": codex("stop"),
            "PreToolUse": codex("tool"),
            "PostToolUse": codex("tool"),
        } if install else {})
    if (HOME / ".cursor").exists():
        configure(HOME / ".cursor/hooks.json", {
            "afterAgentResponse": {"command": command("cursor", "response")},
        } if install else {}, extra={"version": 1})


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("install", "remove"):
        sys.exit(__doc__)
    main(sys.argv[1])
