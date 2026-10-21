"""Read the clipboard. Only called when the user clicks "Import from clipboard"."""
import tkinter


def read_clipboard() -> str:
    root = tkinter.Tk()
    root.withdraw()
    try:
        return root.clipboard_get()
    except tkinter.TclError:
        return ""          # clipboard empty or not text
    finally:
        root.destroy()
