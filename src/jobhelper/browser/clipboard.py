"""Read or write the clipboard. Only called when the user clicks a button that says so.

Windows uses the system clipboard directly, so text written here can be pasted into any other
program straight away. (tkinter hands text over lazily and it vanishes when its helper window
closes, which left other programs waiting or pasting nothing.) macOS uses pbcopy/pbpaste.
Linux uses xclip, xsel or wl-clipboard when present, else tkinter. Only Windows has been tested.
"""
import shutil
import subprocess
import sys
import time

_CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 0x0002


# -- Windows ------------------------------------------------------------------------------------
def _win_api():
    import ctypes
    from ctypes import wintypes

    user32, kernel32 = ctypes.WinDLL("user32"), ctypes.WinDLL("kernel32")
    user32.OpenClipboard.argtypes, user32.OpenClipboard.restype = [wintypes.HWND], wintypes.BOOL
    user32.CloseClipboard.argtypes, user32.CloseClipboard.restype = [], wintypes.BOOL
    user32.EmptyClipboard.argtypes, user32.EmptyClipboard.restype = [], wintypes.BOOL
    user32.GetClipboardData.argtypes, user32.GetClipboardData.restype = [wintypes.UINT], ctypes.c_void_p
    user32.SetClipboardData.argtypes, user32.SetClipboardData.restype = [wintypes.UINT, ctypes.c_void_p], ctypes.c_void_p
    kernel32.GlobalAlloc.argtypes, kernel32.GlobalAlloc.restype = [wintypes.UINT, ctypes.c_size_t], ctypes.c_void_p
    kernel32.GlobalLock.argtypes, kernel32.GlobalLock.restype = [ctypes.c_void_p], ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes, kernel32.GlobalUnlock.restype = [ctypes.c_void_p], wintypes.BOOL
    kernel32.GlobalFree.argtypes, kernel32.GlobalFree.restype = [ctypes.c_void_p], ctypes.c_void_p
    return ctypes, user32, kernel32


def _win_open(user32) -> None:
    for _ in range(20):                      # another program may hold the clipboard for a moment
        if user32.OpenClipboard(None):
            return
        time.sleep(0.05)
    raise OSError("The clipboard is busy; try again.")


def _win_read() -> str:
    ctypes, user32, kernel32 = _win_api()
    _win_open(user32)
    try:
        handle = user32.GetClipboardData(_CF_UNICODETEXT)
        if not handle:
            return ""                        # empty, or not text
        pointer = kernel32.GlobalLock(handle)
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def _win_write(text: str) -> None:
    ctypes, user32, kernel32 = _win_api()
    data = text.encode("utf-16-le") + b"\x00\x00"
    handle = kernel32.GlobalAlloc(_GMEM_MOVEABLE, len(data))
    if not handle:
        raise OSError("Out of memory while copying.")
    pointer = kernel32.GlobalLock(handle)
    ctypes.memmove(pointer, data, len(data))
    kernel32.GlobalUnlock(handle)
    _win_open(user32)
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(_CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            raise OSError("Could not put the text on the clipboard.")
    finally:
        user32.CloseClipboard()              # on success the system now owns the memory


# -- other platforms --------------------------------------------------------------------------------
def _tk_read() -> str:
    import tkinter

    root = tkinter.Tk()
    root.withdraw()
    try:
        return root.clipboard_get()
    except tkinter.TclError:
        return ""
    finally:
        root.destroy()


def _tk_write(text: str) -> None:
    import tkinter

    root = tkinter.Tk()
    root.withdraw()
    try:
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()
    finally:
        root.destroy()


def _linux_tools():
    for read, write in ((["wl-paste", "--no-newline"], ["wl-copy"]),
                        (["xclip", "-selection", "clipboard", "-o"], ["xclip", "-selection", "clipboard"]),
                        (["xsel", "--clipboard", "--output"], ["xsel", "--clipboard", "--input"])):
        if shutil.which(read[0]):
            return read, write
    return None


def read_clipboard() -> str:
    if sys.platform == "win32":
        return _win_read()
    if sys.platform == "darwin":
        return subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=10).stdout
    tools = _linux_tools()
    if tools:
        return subprocess.run(tools[0], capture_output=True, text=True, timeout=10).stdout
    return _tk_read()


def write_clipboard(text: str) -> None:
    if sys.platform == "win32":
        return _win_write(text)
    if sys.platform == "darwin":
        subprocess.run(["pbcopy"], input=text, text=True, timeout=10, check=True)
        return
    tools = _linux_tools()
    if tools:
        subprocess.run(tools[1], input=text, text=True, timeout=10, check=True)
        return
    _tk_write(text)
