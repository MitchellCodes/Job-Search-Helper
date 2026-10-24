"""The functions the screens call (exposed to the window via pywebview, or over HTTP in dev mode).

Every public method returns {"ok": True, "data": ...} or {"ok": False, "error": "..."} so the
JavaScript never has to deal with exceptions. Names starting with "_" are private and are
not exposed to the window.
"""
import functools
import threading

from jobhelper.browser import pane
from jobhelper.service import JobHelper


def _safe(fn):
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            try:
                return {"ok": True, "data": fn(self, *args, **kwargs)}
            except (ValueError, FileNotFoundError) as e:
                return {"ok": False, "error": str(e)}
            except Exception as e:  # noqa: BLE001 - never crash the window on a bad action
                return {"ok": False, "error": f"Unexpected error: {type(e).__name__}: {e}"}

    return wrapper


class Api:
    def __init__(self, helper: JobHelper | None = None, browser=None, clipboard=None, file_picker=None,
                 clipboard_write=None):
        self._helper = helper or JobHelper()
        self._browser = browser
        self._clipboard = clipboard
        self._clipboard_write = clipboard_write
        self._file_picker = file_picker      # () -> path | None   (native dialog, set by app.py)
        self._lock = threading.RLock()

    # -- overview ------------------------------------------------------------------
    @_safe
    def state(self):
        h = self._helper
        resume = h.get_resume()
        return {
            "resume": ({"id": resume["id"], "name": resume["name"], "filename": resume["filename"],
                        "years_experience": resume["years_experience"],
                        "skills_found": sorted(h.vocab.find(resume["raw_text"]))} if resume else None),
            "search": h.get_search(),
            "listing_count": h.conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0],
            "model_available": h.embedder is not None,
            "browser_available": self._browser is not None,
        }

    # -- resume --------------------------------------------------------------------
    @_safe
    def choose_resume(self):
        if self._file_picker is None:
            raise ValueError("File picker is not available here; use upload_resume_path.")
        path = self._file_picker()
        if not path:
            return None
        return self._helper.import_resume(path)

    @_safe
    def upload_resume_path(self, path: str):
        return self._helper.import_resume(path)

    @_safe
    def get_resume(self):
        return self._helper.get_resume()

    @_safe
    def save_resume_text(self, resume_id: int, text: str):
        self._helper.update_resume_text(int(resume_id), text)
        return self._helper.get_resume(int(resume_id))

    # -- search context ------------------------------------------------------------
    @_safe
    def set_search(self, titles, levels):
        return self._helper.set_search(list(titles), list(levels))

    @_safe
    def search_links(self, location: str = ""):
        s = self._helper.get_search()
        if not s["titles"]:
            raise ValueError("Enter at least one job title first.")
        level = s["levels"][0] if len(s["levels"]) == 1 else None
        return {t: pane.search_links(t, level, location) for t in s["titles"]}

    # -- importing listings -----------------------------------------------------------
    @_safe
    def import_text(self, text: str, url: str | None = None):
        return self._helper.import_listing(text, url or None)

    @_safe
    def import_bulk(self, text: str):
        results = self._helper.import_listings_bulk(text)
        if not results:
            raise ValueError("No listings found. Separate listings with a line containing ====")
        return results

    @_safe
    def import_clipboard(self, url: str | None = None):
        """Import the text the user copied. `url` is the address shown in the app (the user may have
        edited it); if it's empty the job-site window's current address is used."""
        if self._clipboard is None:
            raise ValueError("Clipboard access isn't available here.")
        text = self._clipboard()
        if not text.strip():
            raise ValueError("The clipboard is empty. In the job site window, select the listing text "
                             "(Ctrl+A selects the whole page) and copy it with Ctrl+C, then try again.")
        url = (url or "").strip() or (self._browser.current_url() if self._browser else None)
        return self._helper.import_listing(text, url)

    @_safe
    def browser_status(self):
        """Is the job-site window open, and which address is it showing right now?"""
        if self._browser is None:
            return {"available": False, "open": False, "url": None}
        return {"available": True, "open": self._browser.is_open(), "url": self._browser.current_url()}

    @_safe
    def copy_text(self, text: str):
        if self._clipboard_write is None:
            raise ValueError("Copying isn't available here; select the text and press Ctrl+C.")
        self._clipboard_write(str(text))

    # -- companion browser ---------------------------------------------------------------
    @_safe
    def browser_open(self, url: str):
        if self._browser is None:
            raise ValueError("The companion browser isn't available here.")
        self._browser.open(url)

    @_safe
    def browser_action(self, action: str):
        if self._browser is None:
            raise ValueError("The companion browser isn't available here.")
        if action not in ("back", "forward", "reload", "dock", "close"):
            raise ValueError(f"Unknown browser action: {action}")
        getattr(self._browser, action)()

    # -- listings ---------------------------------------------------------------------------
    @_safe
    def ranked(self, min_score: float = 0, level: str | None = None, remote: str | None = None,
               exclude_words=None):
        return self._helper.ranked_listings(min_score=float(min_score or 0), level=level or None,
                                            remote=remote or None, exclude_words=exclude_words or None)

    @_safe
    def listing_detail(self, listing_id: int):
        listing = self._helper.get_listing(int(listing_id))
        if listing is None:
            raise ValueError("That listing no longer exists.")
        return {"listing": listing, "score": self._helper.score(int(listing_id))}

    @_safe
    def set_feedback(self, listing_id: int, feedback):
        self._helper.set_feedback(int(listing_id), feedback or None)

    @_safe
    def set_listing_tags(self, listing_id: int, title, level):
        self._helper.set_listing_tags(int(listing_id), title or None, level or None)

    @_safe
    def delete_listing(self, listing_id: int):
        self._helper.delete_listing(int(listing_id))

    # -- vocabulary & role profiles --------------------------------------------------------------
    @_safe
    def term_candidates(self):
        return self._helper.new_term_candidates()

    @_safe
    def accept_term(self, term: str, category: str = "skill", merge_into: str | None = None):
        self._helper.accept_term(term, category, merge_into or None)

    @_safe
    def ignore_term(self, term: str):
        self._helper.ignore_term(term)

    @_safe
    def skill_names(self):
        return sorted(self._helper.vocab.terms)

    @_safe
    def role_options(self):
        return self._helper.role_options()

    @_safe
    def role_profile(self, title: str, level: str | None = None):
        return self._helper.role_profile(title, level or None)

    # -- data ---------------------------------------------------------------------------------------
    @_safe
    def delete_all_data(self, confirm: bool = False):
        if confirm is not True:
            raise ValueError("Deleting all data requires confirmation.")
        self._helper.delete_all_data()


PUBLIC_METHODS = {n for n in dir(Api) if not n.startswith("_") and callable(getattr(Api, n))}
