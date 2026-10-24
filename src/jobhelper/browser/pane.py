"""Option A browser pane: a companion window docked beside the main window.

The user drives it like any browser. The app never reads the page's contents and never
clicks, scrolls or navigates on the user's behalf; it only opens the address the user asks
for and reports the current address. Option B (single embedded Qt browser) would replace
this class and keep the same methods (see ROADMAP.md).
"""
from urllib.parse import quote_plus

SEARCH_SITES = {
    "LinkedIn": "https://www.linkedin.com/jobs/search/?keywords={q}&location={loc}",
    "Indeed": "https://www.indeed.com/jobs?q={q}&l={loc}",
    "ZipRecruiter": "https://www.ziprecruiter.com/jobs-search?search={q}&location={loc}",
    "Glassdoor": "https://www.glassdoor.com/Job/jobs.htm?sc.keyword={q}",
}
LEVEL_WORDS = {"entry": "entry level", "mid": "", "senior": "senior", "lead": "lead"}


def search_links(title: str, level: str | None = None, location: str = "") -> dict[str, str]:
    """Each site's own search page pre-filled with the user's title/level/location."""
    words = " ".join(w for w in (LEVEL_WORDS.get(level or "", ""), title.strip()) if w)
    return {site: url.format(q=quote_plus(words), loc=quote_plus(location)) for site, url in SEARCH_SITES.items()}


def normalise_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise ValueError("Enter a web address.")
    if "://" not in url:
        url = "https://" + url
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("Only http/https addresses can be opened.")
    return url


def enable_context_menu(window) -> bool:
    """Turn on the right-click menu (Copy, Copy link address, Back...) for one window.

    pywebview only enables it in debug mode, which would also switch on developer tools, so
    the WebView2 setting is flipped directly. Windows (WebView2) only; returns False elsewhere.
    """
    try:
        from System import Action

        native = window.native

        def apply():
            native.webview.CoreWebView2.Settings.AreDefaultContextMenusEnabled = True

        native.webview.Invoke(Action(apply))   # WebView2 settings must be changed on the UI thread
        return True
    except Exception:  # noqa: BLE001 - other platforms / not ready yet: selection and Ctrl+C still work
        return False


class CompanionBrowser:
    def __init__(self):
        self.window = None
        self.main = None

    def attach(self, main_window) -> None:
        self.main = main_window

    def _alive(self) -> bool:
        return self.window is not None

    def open(self, url: str) -> None:
        import webview

        url = normalise_url(url)
        if self._alive():
            self.window.load_url(url)
            return
        x, y, w, h = self._dock_rect()
        # text_select=True: without it pywebview blocks selecting text, so nothing can be copied
        self.window = webview.create_window("Job site", url, x=x, y=y, width=w, height=h, text_select=True)
        self.window.events.closed += self._on_closed
        self.window.events.loaded += lambda: enable_context_menu(self.window) if self.window else None

    def _on_closed(self) -> None:
        self.window = None

    def _dock_rect(self):
        m = self.main
        if m is None:
            return 760, 60, 900, 700
        return m.x + m.width, m.y, max(600, m.width), m.height

    def dock(self) -> None:
        if self._alive():
            x, y, w, h = self._dock_rect()
            self.window.move(x, y)
            self.window.resize(w, h)

    def back(self) -> None:
        if self._alive():
            self.window.evaluate_js("history.back()")

    def forward(self) -> None:
        if self._alive():
            self.window.evaluate_js("history.forward()")

    def reload(self) -> None:
        if self._alive():
            self.window.evaluate_js("location.reload()")

    def is_open(self) -> bool:
        return self._alive()

    def current_url(self) -> str | None:
        if not self._alive():
            return None
        try:
            return self.window.get_current_url()
        except Exception:  # noqa: BLE001 - window closing or still loading
            return None

    def close(self) -> None:
        if self._alive():
            self.window.destroy()
            self.window = None
