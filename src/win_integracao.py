"""Integração com o Windows: área de transferência, teclas e atalhos globais (só ctypes, sem pacotes)."""
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


# ---------------------------------------------------------------- área de transferência

def _abrir_clipboard() -> bool:
    for _ in range(25):  # outro programa pode estar com ela aberta por um instante
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def ler_clipboard() -> str | None:
    """Texto da área de transferência, ou None se estiver vazia ou tiver outra coisa (imagem etc.)."""
    if not _abrir_clipboard():
        return None
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        ponteiro = kernel32.GlobalLock(handle)
        try:
            return ctypes.wstring_at(ponteiro)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def escrever_clipboard(texto: str) -> bool:
    if not _abrir_clipboard():
        return False
    try:
        user32.EmptyClipboard()
        dados = ctypes.create_unicode_buffer(texto)
        tamanho = ctypes.sizeof(dados)
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, tamanho)
        ponteiro = kernel32.GlobalLock(handle)
        ctypes.memmove(ponteiro, ctypes.addressof(dados), tamanho)
        kernel32.GlobalUnlock(handle)
        return bool(user32.SetClipboardData(CF_UNICODETEXT, handle))
    finally:
        user32.CloseClipboard()


def limpar_clipboard() -> None:
    if _abrir_clipboard():
        try:
            user32.EmptyClipboard()
        finally:
            user32.CloseClipboard()


def sequencia_clipboard() -> int:
    return int(user32.GetClipboardSequenceNumber())


# ---------------------------------------------------------------- teclas

def _tecla(vk: int, soltar: bool = False) -> None:
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP if soltar else 0, 0)


def esperar_soltar_modificadores(limite: float = 1.5) -> None:
    """O atalho global ainda está com Ctrl/Alt apertados; um Ctrl+C agora viraria Ctrl+Alt+C."""
    fim = time.time() + limite
    while time.time() < fim and any(user32.GetAsyncKeyState(vk) & 0x8000
                                    for vk in (VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN)):
        time.sleep(0.02)


def copiar_selecao() -> None:
    _tecla(VK_CONTROL)
    _tecla(VK_C)
    _tecla(VK_C, soltar=True)
    _tecla(VK_CONTROL, soltar=True)


def texto_selecionado() -> str:
    """Seleção do app em foco: manda Ctrl+C, lê e devolve o texto que estava na área de transferência.
    Se ela tinha uma imagem ou outro formato, esse conteúdo não volta."""
    anterior = ler_clipboard()
    antes = sequencia_clipboard()
    esperar_soltar_modificadores()
    time.sleep(0.05)
    copiar_selecao()
    for _ in range(30):
        if sequencia_clipboard() != antes:
            break
        time.sleep(0.03)
    else:
        return ""  # nada mudou: não havia seleção
    texto = ler_clipboard() or ""
    if anterior is not None:
        escrever_clipboard(anterior)
    return texto


# ---------------------------------------------------------------- atalhos globais

NOMES_TECLAS = {"esc": 0x1B, "escape": 0x1B, "space": 0x20, "espaco": 0x20, "enter": 0x0D,
                "tab": 0x09, "pause": 0x13}


def analisar_atalho(spec: str) -> tuple[int, int]:
    """'ctrl+alt+p' -> (modificadores, código da tecla virtual)."""
    mods, vk = 0, None
    for parte in (p.strip().lower() for p in spec.split("+")):
        if parte in ("ctrl", "control"):
            mods |= MOD_CONTROL
        elif parte == "alt":
            mods |= MOD_ALT
        elif parte == "shift":
            mods |= MOD_SHIFT
        elif parte in ("win", "windows"):
            mods |= MOD_WIN
        elif parte in NOMES_TECLAS:
            vk = NOMES_TECLAS[parte]
        elif len(parte) == 1 and parte.isalnum():
            vk = ord(parte.upper())
        elif parte.startswith("f") and parte[1:].isdigit() and 1 <= int(parte[1:]) <= 24:
            vk = 0x70 + int(parte[1:]) - 1
        else:
            raise ValueError(f"tecla desconhecida em '{spec}': {parte}")
    if vk is None:
        raise ValueError(f"atalho sem tecla: '{spec}'")
    return mods, vk


def registrar_atalhos(atalhos: dict) -> dict:
    """Registra atalhos globais {'ctrl+alt+p': função}. Devolve {atalho: True/False}, em que False
    significa que outro programa já usa essa combinação."""
    resultado, pronto = {}, threading.Event()

    def laco() -> None:
        funcoes = {}
        for i, (spec, funcao) in enumerate(atalhos.items(), 1):
            try:
                mods, vk = analisar_atalho(spec)
                resultado[spec] = bool(user32.RegisterHotKey(None, i, mods | MOD_NOREPEAT, vk))
            except ValueError:
                resultado[spec] = False
            funcoes[i] = funcao
        pronto.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam in funcoes:
                threading.Thread(target=funcoes[msg.wParam], daemon=True).start()

    threading.Thread(target=laco, daemon=True).start()
    pronto.wait(3)
    return resultado
