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


def run_window() -> None:
    import webview

    from jobhelper.api import Api
    from jobhelper.browser.clipboard import read_clipboard, write_clipboard
    from jobhelper.browser.pane import CompanionBrowser, enable_context_menu

    browser = CompanionBrowser()
    holder = {}

    def pick_file():
        result = holder["window"].create_file_dialog(
            webview.OPEN_DIALOG, file_types=("Resume files (*.pdf;*.docx)", "All files (*.*)"))
        return result[0] if result else None

    api = Api(browser=browser, clipboard=read_clipboard, file_picker=pick_file, clipboard_write=write_clipboard)
    # text_select=True so text in the app (scores, listing text, addresses) can be selected and copied
    window = webview.create_window("Job Search Helper", str(WEB_INDEX), js_api=api, width=1000, height=760,
                                   min_size=(760, 560), text_select=True)
    holder["window"] = window
    window.events.loaded += lambda: enable_context_menu(window)
    browser.attach(window)
    window.events.closed += browser.close
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
    _say("SMOKE python state ok:", api.state()["ok"])
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
