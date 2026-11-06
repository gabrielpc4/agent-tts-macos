#!/usr/bin/env python3
"""Menu bar icon to control tts (voice language, voice, speed, on/off)."""
import os
import subprocess
import threading
import time

import AppKit
import rumps
from quickmachotkey import mask, quickHotKey
from quickmachotkey.constants import kVK_ANSI_P, kVK_Escape, optionKey

import tts

ICON_ON, ICON_SPEAKING, ICON_OFF, ICON_PAUSED = "🔊", "🗣️", "🔇", "⏸️"
SPEEDS = [round(1.0 + i / 10, 1) for i in range(11)]  # 1.0 … 2.0
MODELS = {
    "gemini-3.8-flash-tts": "Flash (best quality)",
    "gemini-3.8-flash-lite-tts": "Flash-Lite (cheaper)",
}
MODEL_SHORT_NAMES = {"gemini-3.8-flash-tts": "Flash", "gemini-3.8-flash-lite-tts": "Flash-Lite"}
SYSTEM = ""  # the "language" value that follows the system language


def speaking() -> bool:
    try:
        os.kill(int(tts.PIDFILE.read_text(encoding="utf-8")), 0)
        return True
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return False


class App(rumps.App):
    def __init__(self):
        super().__init__("Agent TTS", title=ICON_ON, quit_button=None)
        cfg = tts.read_config()

        self.menu_language = rumps.MenuItem("Voice language")
        system_name = tts.LANGUAGES[tts.system_language()]["name"]
        system = rumps.MenuItem(f"System language ({system_name})", callback=self.change_language)
        system.language = SYSTEM
        self.menu_language.add(system)
        self.menu_language.add(rumps.separator)
        for code, language in tts.LANGUAGES.items():
            item = rumps.MenuItem(language["name"], callback=self.change_language)
            item.language = code
            self.menu_language.add(item)

        self.item_enabled = rumps.MenuItem("Reading on", callback=self.toggle)
        self.item_progress = rumps.MenuItem("Read progress notes", callback=self.toggle_progress)
        self.item_subagents = rumps.MenuItem("Read Codex subagents", callback=self.toggle_subagents)
        self.item_status = rumps.MenuItem("")

        self.menu_voice = rumps.MenuItem("Voice")
        for group, gender in (("Female", "female"), ("Male", "male")):
            self.menu_voice.add(rumps.MenuItem(group))
            for name, (voice_gender, description) in tts.GEMINI_VOICES.items():
                if voice_gender == gender:
                    item = rumps.MenuItem(f"{name} — {description}", callback=self.change_voice)
                    item.voice = name
                    self.menu_voice.add(item)
            self.menu_voice.add(rumps.separator)

        self.menu_speed = rumps.MenuItem("Speed")
        for v in SPEEDS:
            item = rumps.MenuItem(f"{v:.1f}x" + ("  (base fast pace)" if v == 1.0 else ""),
                                  callback=self.change_speed)
            item.speed = v
            self.menu_speed.add(item)

        self.menu_conversations = rumps.MenuItem("Conversations")
        self.menu_conversations.add(rumps.MenuItem("…"))  # rumps only creates the submenu when it gets a first item
        self.conversations_signature = None

        self.menu_model = rumps.MenuItem("Gemini model")
        for model_id, description in MODELS.items():
            item = rumps.MenuItem(description, callback=self.change_model)
            item.model = model_id
            self.menu_model.add(item)

        self.menu = [
            self.menu_language,
            None,
            rumps.MenuItem("Speak selection  (⌥Esc)", callback=self.speak_selection),
            rumps.MenuItem("Pause / resume  (⌥P)", callback=self.pause),
            rumps.MenuItem("Speak now", callback=self.speak_now),
            self.item_enabled,
            self.item_progress,
            self.item_subagents,
            self.menu_conversations,
            rumps.MenuItem("Stop speaking", callback=self.stop, key="."),
            rumps.MenuItem("Repeat last", callback=self.repeat, key="r"),
            rumps.MenuItem("Test voice", callback=self.test, key="t"),
            None,
            self.menu_voice,
            self.menu_speed,
            self.menu_model,
            None,
            self.item_status,
            rumps.MenuItem("Open log", callback=self.open_log),
            rumps.MenuItem("Quit", callback=rumps.quit_application),
        ]
        self.update_marks(cfg)
        self.notice, self.notice_until = "", 0.0
        rumps.Timer(self.tick, 1).start()

    # ------------------------------------------------------------ visual state

    def update_marks(self, cfg=None):
        cfg = cfg or tts.read_config()
        language = tts.current_language(cfg)
        for item in self.menu_language.values():
            if hasattr(item, "language"):
                item.state = int(item.language == (cfg.get("language") or SYSTEM))
        self.menu_language.title = f"Voice language: {tts.LANGUAGES[language]['name']}"
        self.item_enabled.state = int(cfg["enabled"])
        self.item_progress.state = int(cfg.get("progress_notes", True))
        self.item_subagents.state = int(cfg.get("codex_subagents", False))
        for item in self.menu_voice.values():
            if hasattr(item, "voice"):
                item.state = int(item.voice == cfg["voice"])
        for item in self.menu_speed.values():
            item.state = int(abs(item.speed - cfg["speed"]) < 0.01)
        for item in self.menu_model.values():
            item.state = int(item.model == cfg["model"])
        self.menu_voice.title = f"Voice: {cfg['voice']}"
        self.menu_speed.title = f"Speed: {cfg['speed']:.1f}x"
        self.update_conversations(cfg)

    def update_conversations(self, cfg):
        """Rebuilds the submenu only when the list or the marks change (rebuilding it while open makes it flicker)."""
        active = tts.active_conversations()
        chosen = cfg.get("chosen_conversations", [])
        only = cfg.get("only_chosen_conversations", False)
        signature = (only, tuple((c["id"], tts.conversation_name(c), c["id"] in chosen) for c in active))
        total = sum(c["id"] in chosen for c in active)
        self.menu_conversations.title = f"Conversations: {total} of {len(active)}" if only else "Conversations: all"
        if signature == self.conversations_signature:
            return
        self.conversations_signature = signature
        # rumps keeps every item it creates in a class dictionary (NSApp._ns_to_py_and_callback) and clear()
        # doesn't remove them from it: without this, each rebuild of the submenu kept the old items in memory
        # forever (1.3 million NSMenuItem and 2 GB after 29 hours).
        registry = rumps.rumps.NSApp._ns_to_py_and_callback
        for item in self.menu_conversations.values():
            registry.pop(getattr(item, "_menuitem", None), None)
        self.menu_conversations.clear()
        only_chosen = rumps.MenuItem("Hear only the chosen ones", callback=self.toggle_only_chosen)
        only_chosen.state = int(only)
        self.menu_conversations.add(only_chosen)
        self.menu_conversations.add(rumps.separator)
        if not active:
            self.menu_conversations.add(rumps.MenuItem("(no active conversation right now)"))
        for c in active:
            item = rumps.MenuItem(tts.conversation_name(c), callback=self.toggle_conversation)
            item.conversation = c["id"]
            item.state = int(c["id"] in chosen)
            self.menu_conversations.add(item)

    def tick(self, _):
        cfg = tts.read_config()
        self.title = (ICON_PAUSED if tts.PAUSED.exists() else ICON_OFF if not cfg["enabled"]
                      else ICON_SPEAKING if speaking() else ICON_ON)
        if time.time() < self.notice_until:
            self.item_status.title = self.notice
        elif (tts.BASE / "no-key-tts-gemini").exists():
            self.item_status.title = "⚠️ No Gemini key in the login keychain: using Microsoft"
        elif pauses := [(model, until) for model in tts.GEMINI_MODELS if (until := tts.paused_until(model))]:
            fallback = "Microsoft" if len(pauses) == len(tts.GEMINI_MODELS) else \
                next(MODEL_SHORT_NAMES[m] for m in tts.GEMINI_MODELS if m not in dict(pauses))
            model, until = pauses[0]
            self.item_status.title = (f"⚠️ {MODEL_SHORT_NAMES[model]} at its daily limit until "
                                      f"{time.strftime('%H:%M', time.localtime(until))}, using {fallback}")
        else:
            queued = len(list(tts.QUEUE.glob("*.txt"))) if tts.QUEUE.exists() else 0
            self.item_status.title = f"Gemini ok · queued: {queued}"
        self.update_marks(cfg)

    # ------------------------------------------------------------ actions

    def save(self, **changes):
        cfg = tts.read_config()
        cfg.update(changes)
        tts.save_config(cfg)
        self.update_marks(cfg)

    def introduce(self):
        """Plays the test sentence with the new setting, interrupting what was playing."""
        def run():
            tts.stop()
            tts.enqueue(tts.test_sentence())
        threading.Thread(target=run, daemon=True).start()

    def change_language(self, item):
        self.save(language=item.language)
        self.introduce()

    def toggle(self, _):
        enabled = not tts.read_config()["enabled"]
        self.save(enabled=enabled)
        if not enabled:
            threading.Thread(target=tts.stop, daemon=True).start()

    def toggle_progress(self, _):
        self.save(progress_notes=not tts.read_config().get("progress_notes", True))

    def toggle_subagents(self, _):
        self.save(codex_subagents=not tts.read_config().get("codex_subagents", False))

    def toggle_only_chosen(self, _):
        self.save(only_chosen_conversations=not tts.read_config().get("only_chosen_conversations", False))

    def toggle_conversation(self, item):
        if not tts.read_config().get("only_chosen_conversations"):
            self.save(only_chosen_conversations=True)  # choosing a conversation only makes sense in "only the chosen"
        tts.toggle_conversation(item.conversation)
        self.update_marks()

    def change_voice(self, item):
        self.save(voice=item.voice)
        self.introduce()

    def change_speed(self, item):
        self.save(speed=item.speed)
        self.repeat(None)  # hear it right away, without spending API

    def change_model(self, item):
        self.save(model=item.model)

    def stop(self, _):
        threading.Thread(target=tts.stop, daemon=True).start()

    def repeat(self, _):
        threading.Thread(target=tts.repeat_last, daemon=True).start()

    def speak_selection(self, _):
        threading.Thread(target=tts.speak_selection, daemon=True).start()

    def pause(self, _):
        threading.Thread(target=tts.toggle_pause, daemon=True).start()

    def speak_now(self, _):
        def find():
            self.notice = tts.speak_now()
            self.notice_until = time.time() + 6
        threading.Thread(target=find, daemon=True).start()

    def test(self, _):
        tts.enqueue(tts.test_sentence())

    def open_log(self, _):
        tts.LOG.touch()
        subprocess.run(["open", "-a", "TextEdit", str(tts.LOG)])


@quickHotKey(virtualKey=kVK_ANSI_P, modifierMask=mask(optionKey))
def pause_shortcut() -> None:
    """⌥P in any app: pause or resume speech."""
    threading.Thread(target=tts.toggle_pause, daemon=True).start()


@quickHotKey(virtualKey=kVK_Escape, modifierMask=mask(optionKey))
def selection_shortcut() -> None:
    """⌥Esc in any app: read the selection with this app's voice (or stop, if it is already speaking)."""
    threading.Thread(target=tts.speak_selection, daemon=True).start()


if __name__ == "__main__":
    # on launch (login or restart) it starts off and without a leftover pause
    tts.save_config(dict(tts.read_config(), enabled=False))
    tts.PAUSED.unlink(missing_ok=True)
    import ApplicationServices
    if not ApplicationServices.AXIsProcessTrustedWithOptions({"AXTrustedCheckOptionPrompt": True}):
        tts.log("menu bar without accessibility permission: Speak selection will ask on first use")
    AppKit.NSApplication.sharedApplication().setActivationPolicy_(
        AppKit.NSApplicationActivationPolicyAccessory)  # no Dock icon
    App().run()
