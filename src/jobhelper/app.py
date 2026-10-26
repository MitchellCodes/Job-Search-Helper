"""Starts the program and opens the window."""
import os
import sys
import time
from pathlib import Path

WEB_INDEX = Path(__file__).resolve().parent / "web" / "index.html"


def _arg(name: str, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        return sys.argv[i + 1] if i + 1 < len(sys.argv) else default
    return default


def enable_context_menu(window) -> bool:
    """Turn on the right-click menu (Copy, Paste...) for the window.

    pywebview only enables it in debug mode, which would also switch on developer tools, so the
    WebView2 setting is flipped directly. Windows (WebView2) only; returns False elsewhere.
    """
    try:
        from System import Action

        native = window.native

        def apply():
            native.webview.CoreWebView2.Settings.AreDefaultContextMenusEnabled = True

        native.webview.Invoke(Action(apply))   # WebView2 settings must be changed on the UI thread
        return True
    except Exception:  # noqa: BLE001 - other platforms / not ready yet: Ctrl+C and Ctrl+V still work
        return False


def _first(result):
    if not result:
        return None
    return result[0] if isinstance(result, (list, tuple)) else result


def run_window() -> None:
    import webview

    from jobhelper.api import Api

    holder = {}

    def pick_file():
        return _first(holder["window"].create_file_dialog(
            webview.OPEN_DIALOG, file_types=("Resume files (*.pdf;*.docx)", "All files (*.*)")))

    def save_file(suggested_name: str):
        return _first(holder["window"].create_file_dialog(
            webview.SAVE_DIALOG, save_filename=suggested_name, file_types=("Word document (*.docx)",)))

    api = Api(file_picker=pick_file, file_saver=save_file)
    # text_select=True so text in the app (scores, listing text, suggestions) can be selected and copied
    window = webview.create_window("Job Search Helper", str(WEB_INDEX), js_api=api, width=1100, height=800,
                                   min_size=(760, 560), text_select=True)
    holder["window"] = window
    window.events.loaded += lambda: enable_context_menu(window)
    smoke = "--smoke" in sys.argv
    # `--smoke` opens the real window, checks the bridge answers, then exits (used to test builds).
    webview.start(_smoke_check if smoke else None, (window, api) if smoke else None)


def _say(*parts) -> None:
    line = " ".join(str(p) for p in parts)
    print(line, flush=True)
    log = os.environ.get("JOBHELPER_SMOKE_LOG")      # lets a windowed (console-less) build report
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def _smoke_check(window, api) -> None:
    """Open the real window, then verify from inside the page that the Python bridge works."""
    time.sleep(3)
    state = api.state()
    _say("SMOKE python state ok:", state["ok"])
    # proves the packaged app can start Claude Code (only `claude --version`; no request is sent)
    _say("SMOKE claude:", state["data"]["claude"] if state["ok"] else state)
    # evaluate_js does not wait for promises: start the call, then read the stored result back
    window.evaluate_js("window.__smoke = 'pending'; window.pywebview.api.state()"
                       ".then(r => { window.__smoke = JSON.stringify({ok: r.ok, model: r.data.model_available}); })"
                       ".catch(e => { window.__smoke = 'error: ' + e; });")
    time.sleep(1.5)
    _say("SMOKE page->python bridge:", window.evaluate_js("window.__smoke"))
    _say("SMOKE page dom:", window.evaluate_js(
        "document.querySelectorAll('#nav button').length + ' nav buttons; h1=' + "
        "(document.querySelector('h1') ? document.querySelector('h1').textContent : 'none') + "
        "'; status=' + document.querySelector('#status').textContent"))
    window.destroy()


def main() -> None:
    if "--dev" in sys.argv:
        from jobhelper.api import Api
        from jobhelper.devserver import serve

        serve(Api(), int(_arg("--port", 8765)))
    else:
        run_window()


if __name__ == "__main__":
    main()
