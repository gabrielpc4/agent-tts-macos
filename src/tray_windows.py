#!/usr/bin/env python3
"""Windows tray icon to control tts (the counterpart of menubar.py on macOS)."""
import os
import threading
import time

import pystray
from PIL import Image, ImageDraw
from pystray import Menu, MenuItem

import platform_support
import tts
import win_integration

DEFAULT_SHORTCUTS = {"pause_shortcut": "ctrl+alt+p", "selection_shortcut": "ctrl+alt+s"}
SPEEDS = [round(1.0 + i / 10, 1) for i in range(11)]
MODELS = {
    "gemini-3.8-flash-tts": "Flash (best quality)",
    "gemini-3.8-flash-lite-tts": "Flash-Lite (cheaper)",
}
MODEL_SHORT_NAMES = {"gemini-3.8-flash-tts": "Flash", "gemini-3.8-flash-lite-tts": "Flash-Lite"}
COLORS = {"on": (46, 160, 67), "speaking": (31, 111, 235), "paused": (210, 153, 34), "off": (110, 118, 129)}


def cfg() -> dict:
    return tts.read_config()


def shortcut(name: str) -> str:
    return cfg().get(name) or DEFAULT_SHORTCUTS[name]


def speaking() -> bool:
    try:
        return platform_support.process_alive(int(tts.PIDFILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return False


def state() -> str:
    if tts.PAUSED.exists():
        return "paused"
    if not cfg()["enabled"]:
        return "off"
    return "speaking" if speaking() else "on"


def draw_icon(name: str) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, 62, 62), fill=COLORS[name])
    white = (255, 255, 255, 255)
    if name == "paused":
        d.rectangle((20, 18, 27, 46), fill=white)
        d.rectangle((37, 18, 44, 46), fill=white)
        return img
    d.polygon([(14, 26), (24, 26), (36, 16), (36, 48), (24, 38), (14, 38)], fill=white)
    if name in ("on", "speaking"):
        d.arc((32, 22, 46, 42), -50, 50, fill=white, width=4)
        d.arc((36, 16, 54, 48), -50, 50, fill=white, width=4)
    else:
        d.line((14, 50, 50, 14), fill=white, width=5)
    return img


def status_text() -> str:
    pauses = [(m, until) for m in tts.GEMINI_MODELS if (until := tts.paused_until(m))]
    if (tts.BASE / "no-key-tts-gemini").exists():
        return "No Gemini key: using the Microsoft voice"
    if pauses:
        model, until = pauses[0]
        fallback = "Microsoft" if len(pauses) == len(tts.GEMINI_MODELS) else \
            next(MODEL_SHORT_NAMES[m] for m in tts.GEMINI_MODELS if m not in dict(pauses))
        return f"{MODEL_SHORT_NAMES[model]} at its limit until {time.strftime('%H:%M', time.localtime(until))}, using {fallback}"
    queued = len(list(tts.QUEUE.glob("*.txt"))) if tts.QUEUE.exists() else 0
    return f"Gemini ok · queued: {queued}"


def in_thread(function):
    def action(icon=None, item=None):
        threading.Thread(target=function, daemon=True).start()
    return action


def save(**changes) -> None:
    c = cfg()
    c.update(changes)
    tts.save_config(c)


def introduce() -> None:
    """Plays the test sentence with the new setting, interrupting what was playing."""
    def run():
        tts.stop()
        tts.enqueue(tts.test_sentence())
    threading.Thread(target=run, daemon=True).start()


def build_menu() -> Menu:
    def toggle(key, default):
        def action(icon, item):
            save(**{key: not cfg().get(key, default)})
            if key == "enabled" and not cfg()["enabled"]:
                threading.Thread(target=tts.stop, daemon=True).start()
        return action

    def checked(key, default):
        return lambda item: bool(cfg().get(key, default))

    # pystray only accepts functions with exactly 1 argument (check mark) or 2 (action): no default arguments
    def equals(key, value):
        return lambda item: cfg()[key] == value

    def language_equals(value):
        return lambda item: (cfg().get("language") or "") == value

    def speed_equals(value):
        return lambda item: abs(cfg()["speed"] - value) < 0.01

    def choose(key, value):
        def action(icon, item):
            save(**{key: value})
        return action

    def choose_and_introduce(key, value):
        def action(icon, item):
            save(**{key: value})
            introduce()
        return action

    system_name = tts.LANGUAGES[tts.system_language()]["name"]
    languages = [MenuItem(f"System language ({system_name})", choose_and_introduce("language", ""),
                          checked=language_equals(""), radio=True), Menu.SEPARATOR]
    languages += [MenuItem(language["name"], choose_and_introduce("language", code),
                           checked=language_equals(code), radio=True)
                  for code, language in tts.LANGUAGES.items()]

    voices = []
    for group, gender in (("Female", "female"), ("Male", "male")):
        items = [MenuItem(f"{name} — {description}", choose_and_introduce("voice", name),
                          checked=equals("voice", name), radio=True)
                 for name, (voice_gender, description) in tts.GEMINI_VOICES.items() if voice_gender == gender]
        voices.append(MenuItem(group, Menu(*items)))

    def change_speed(v):
        def action(icon, item):
            save(speed=v)
            threading.Thread(target=tts.repeat_last, daemon=True).start()
        return action

    speeds = [MenuItem(f"{v:.1f}x" + ("  (base fast pace)" if v == 1.0 else ""), change_speed(v),
                       checked=speed_equals(v), radio=True)
              for v in SPEEDS]
    models = [MenuItem(description, choose("model", model_id), checked=equals("model", model_id), radio=True)
              for model_id, description in MODELS.items()]

    return Menu(
        MenuItem(lambda item: f"Voice language: {tts.LANGUAGES[tts.current_language(cfg())]['name']}",
                 Menu(*languages)),
        Menu.SEPARATOR,
        MenuItem(lambda item: f"Speak selection  ({shortcut('selection_shortcut')})", in_thread(tts.speak_selection)),
        MenuItem(lambda item: f"Pause / resume  ({shortcut('pause_shortcut')})", in_thread(tts.toggle_pause)),
        MenuItem("Speak now", in_thread(tts.speak_now)),
        Menu.SEPARATOR,
        MenuItem("Reading on", toggle("enabled", False), checked=checked("enabled", False)),
        MenuItem("Read progress notes", toggle("progress_notes", True), checked=checked("progress_notes", True)),
        MenuItem("Read Codex subagents", toggle("codex_subagents", False), checked=checked("codex_subagents", False)),
        MenuItem("Stop speaking", in_thread(tts.stop)),
        MenuItem("Repeat last", in_thread(tts.repeat_last)),
        MenuItem("Test voice", lambda icon, item: tts.enqueue(tts.test_sentence())),
        Menu.SEPARATOR,
        MenuItem("Voice", Menu(*voices)),
        MenuItem("Speed", Menu(*speeds)),
        MenuItem("Gemini model", Menu(*models)),
        Menu.SEPARATOR,
        MenuItem(lambda item: status_text(), None, enabled=False),
        MenuItem("Open log", lambda icon, item: (tts.LOG.touch(), os.startfile(str(tts.LOG)))),
        MenuItem("Quit", lambda icon, item: icon.stop()),
    )


def main() -> None:
    instance = platform_support.FileLock(tts.BASE / "tray.lock")
    tts.BASE.mkdir(exist_ok=True)
    if not instance.try_lock():
        return  # already open
    # on launch (login or restart) it starts off and without a leftover pause
    tts.save_config(dict(cfg(), enabled=False))
    tts.PAUSED.unlink(missing_ok=True)

    icon = pystray.Icon("agent-tts", draw_icon("off"), "Agent TTS", build_menu())
    result = win_integration.register_hotkeys({
        shortcut("pause_shortcut"): tts.toggle_pause,
        shortcut("selection_shortcut"): tts.speak_selection,
    })
    for combination, ok in result.items():
        if not ok:
            tts.log(f"shortcut {combination} unavailable (another program uses it); change it in config.json")

    def refresh() -> None:
        # Only rebuilds the menu when something changed: rebuilding it every second would close the open menu
        last = None
        while True:
            name = state()
            c = cfg()
            signature = (name, status_text(), c["voice"], c["speed"], c["model"], c["enabled"], c.get("language"),
                         c.get("progress_notes", True), c.get("codex_subagents", False))
            if signature != last:
                icon.icon = draw_icon(name)
                icon.title = f"Agent TTS: {name}"
                icon.update_menu()
                last = signature
            time.sleep(1)

    threading.Thread(target=refresh, daemon=True).start()
    icon.run()
    instance.release()


if __name__ == "__main__":
    main()
