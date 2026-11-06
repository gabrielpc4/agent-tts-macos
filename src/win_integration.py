"""Windows integration: clipboard, keys and global shortcuts (ctypes only, no packages)."""
import ctypes
import ctypes.wintypes as wt
import threading
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x0001, 0x0002, 0x0004, 0x0008, 0x4000
KEYEVENTF_KEYUP = 0x0002
VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN, VK_C = 0x10, 0x11, 0x12, 0x5B, 0x5C, 0x43

user32.OpenClipboard.argtypes = [wt.HWND]
user32.OpenClipboard.restype = wt.BOOL
user32.CloseClipboard.restype = wt.BOOL
user32.EmptyClipboard.restype = wt.BOOL
user32.GetClipboardData.argtypes = [wt.UINT]
user32.GetClipboardData.restype = ctypes.c_void_p
user32.SetClipboardData.argtypes = [wt.UINT, ctypes.c_void_p]
user32.SetClipboardData.restype = ctypes.c_void_p
user32.GetClipboardSequenceNumber.restype = wt.DWORD
user32.IsClipboardFormatAvailable.argtypes = [wt.UINT]
user32.IsClipboardFormatAvailable.restype = wt.BOOL
user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
user32.RegisterHotKey.restype = wt.BOOL
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]


# ---------------------------------------------------------------- clipboard

def _open_clipboard() -> bool:
    for _ in range(25):  # another program may have it open for a moment
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def read_clipboard() -> str | None:
    """The clipboard text, or None if it is empty or holds something else (an image etc.)."""
    if not _open_clipboard():
        return None
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        pointer = kernel32.GlobalLock(handle)
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def write_clipboard(text: str) -> bool:
    if not _open_clipboard():
        return False
    try:
        user32.EmptyClipboard()
        data = ctypes.create_unicode_buffer(text)
        size = ctypes.sizeof(data)
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        pointer = kernel32.GlobalLock(handle)
        ctypes.memmove(pointer, ctypes.addressof(data), size)
        kernel32.GlobalUnlock(handle)
        return bool(user32.SetClipboardData(CF_UNICODETEXT, handle))
    finally:
        user32.CloseClipboard()


def clear_clipboard() -> None:
    if _open_clipboard():
        try:
            user32.EmptyClipboard()
        finally:
            user32.CloseClipboard()


def clipboard_sequence() -> int:
    return int(user32.GetClipboardSequenceNumber())


# ---------------------------------------------------------------- keys

def _key(vk: int, release: bool = False) -> None:
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP if release else 0, 0)


def wait_for_modifiers_release(limit: float = 1.5) -> None:
    """The global shortcut still has Ctrl/Alt held down; a Ctrl+C now would become Ctrl+Alt+C."""
    end = time.time() + limit
    while time.time() < end and any(user32.GetAsyncKeyState(vk) & 0x8000
                                    for vk in (VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN)):
        time.sleep(0.02)


def copy_selection() -> None:
    _key(VK_CONTROL)
    _key(VK_C)
    _key(VK_C, release=True)
    _key(VK_CONTROL, release=True)


def selected_text() -> str:
    """Selection of the frontmost app: sends Ctrl+C, reads it and puts back the text that was on the clipboard.
    If the clipboard held an image or another format, that content doesn't come back."""
    previous = read_clipboard()
    before = clipboard_sequence()
    wait_for_modifiers_release()
    time.sleep(0.05)
    copy_selection()
    for _ in range(30):
        if clipboard_sequence() != before:
            break
        time.sleep(0.03)
    else:
        return ""  # nothing changed: there was no selection
    text = read_clipboard() or ""
    if previous is not None:
        write_clipboard(previous)
    return text


# ---------------------------------------------------------------- global shortcuts

KEY_NAMES = {"esc": 0x1B, "escape": 0x1B, "space": 0x20, "enter": 0x0D,
                "tab": 0x09, "pause": 0x13}


def parse_shortcut(spec: str) -> tuple[int, int]:
    """'ctrl+alt+p' -> (modifiers, virtual key code)."""
    mods, vk = 0, None
    for part in (p.strip().lower() for p in spec.split("+")):
        if part in ("ctrl", "control"):
            mods |= MOD_CONTROL
        elif part == "alt":
            mods |= MOD_ALT
        elif part == "shift":
            mods |= MOD_SHIFT
        elif part in ("win", "windows"):
            mods |= MOD_WIN
        elif part in KEY_NAMES:
            vk = KEY_NAMES[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        elif part.startswith("f") and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
            vk = 0x70 + int(part[1:]) - 1
        else:
            raise ValueError(f"unknown key in '{spec}': {part}")
    if vk is None:
        raise ValueError(f"shortcut without a key: '{spec}'")
    return mods, vk


def register_hotkeys(shortcuts: dict) -> dict:
    """Registers global shortcuts {'ctrl+alt+p': function}. Returns {shortcut: True/False}, where False
    means another program already uses that combination."""
    result, ready = {}, threading.Event()

    def loop() -> None:
        functions = {}
        for i, (spec, function) in enumerate(shortcuts.items(), 1):
            try:
                mods, vk = parse_shortcut(spec)
                result[spec] = bool(user32.RegisterHotKey(None, i, mods | MOD_NOREPEAT, vk))
            except ValueError:
                result[spec] = False
            functions[i] = function
        ready.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam in functions:
                threading.Thread(target=functions[msg.wParam], daemon=True).start()

    threading.Thread(target=loop, daemon=True).start()
    ready.wait(3)
    return result
