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
        self.window = webview.create_window("Job site", url, x=x, y=y, width=w, height=h)
        self.window.events.closed += self._on_closed

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

    def current_url(self) -> str | None:
        return self.window.get_current_url() if self._alive() else None

    def close(self) -> None:
        if self._alive():
            self.window.destroy()
            self.window = None
