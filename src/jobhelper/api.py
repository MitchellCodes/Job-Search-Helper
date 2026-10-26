"""The functions the screens call (exposed to the window via pywebview, or over HTTP in dev mode).

Every public method returns {"ok": True, "data": ...} or {"ok": False, "error": "..."} so the
JavaScript never has to deal with exceptions. Names starting with "_" are private and are
not exposed to the window.
"""
import functools
import re
import threading
import time
from pathlib import Path

from jobhelper.paths import data_dir
from jobhelper.service import JobHelper
from jobhelper.tailoring import claude_cli


def _envelope(fn, *args, **kwargs):
    try:
        return {"ok": True, "data": fn(*args, **kwargs)}
    except (ValueError, FileNotFoundError) as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001 - never crash the window on a bad action
        return {"ok": False, "error": f"Unexpected error: {type(e).__name__}: {e}"}


def _safe(fn):
    """Run one request at a time (the database connection is shared between window threads)."""

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return _envelope(fn, self, *args, **kwargs)

    return wrapper


def _safe_unlocked(fn):
    """For slow calls that take the lock themselves only around their database steps."""

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        return _envelope(fn, self, *args, **kwargs)

    return wrapper


def _filename(listing: dict) -> str:
    base = " - ".join(p for p in (listing.get("company"), listing.get("title")) if p) or "listing"
    return "Tailored resume - " + re.sub(r"[^\w .&-]+", "", base).strip()[:60] + ".docx"


class Api:
    def __init__(self, helper: JobHelper | None = None, file_picker=None, file_saver=None):
        self._helper = helper or JobHelper()
        self._file_picker = file_picker      # () -> path | None        (native open dialog, set by app.py)
        self._file_saver = file_saver        # (suggested_name) -> path | None   (native save dialog)
        self._lock = threading.RLock()
        self._claude_cache = (0.0, None)

    def _claude_status(self) -> dict:
        stamp, value = self._claude_cache
        if value is None or time.time() - stamp > 60:
            value = claude_cli.status()
            self._claude_cache = (time.time(), value)
        return value

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
            "claude": self._claude_status(),
            "claude_model": h.claude_model(),
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

    # -- importing listings (pasted text only) ------------------------------------------------
    @_safe
    def import_text(self, text: str, url: str | None = None):
        return self._helper.import_listing(text, url or None)

    @_safe
    def import_bulk(self, text: str):
        results = self._helper.import_listings_bulk(text)
        if not results:
            raise ValueError("No listings found. Separate listings with a line containing ====")
        return results

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

    # -- tailoring with Claude ---------------------------------------------------------------------
    @_safe
    def tailor_preview(self, listing_id: int, extra_terms=None):
        """What would be sent (personal details already replaced). Sends nothing."""
        return self._helper.tailor_preview(int(listing_id), extra_terms=extra_terms)

    @_safe_unlocked
    def tailor_run(self, listing_id: int, extra_terms=None):
        """Send the redacted resume + listing to Claude and return the checked suggestions.

        Takes 10-60 seconds, so the lock is held only for the database steps, not while Claude works.
        """
        with self._lock:
            job = self._helper.tailor_prepare(int(listing_id), extra_terms=extra_terms)
        result = self._helper.tailor_call(job)
        with self._lock:
            return self._helper.tailor_finish(job, result)

    @_safe
    def tailor_latest(self, listing_id: int):
        return self._helper.tailor_latest(int(listing_id))

    @_safe
    def tailor_text(self, tailoring_id: int, accepted_ids=None):
        return self._helper.tailor_text(int(tailoring_id), accepted_ids or [])

    @_safe
    def tailor_export(self, tailoring_id: int, accepted_ids=None):
        record = self._helper.tailor_get(int(tailoring_id))
        if record is None:
            raise ValueError("That tailoring result no longer exists.")
        name = _filename(self._helper.get_listing(record["listing_id"]) or {})
        if self._file_saver is not None:
            chosen = self._file_saver(name)
            if not chosen:
                return None                                   # the user cancelled the dialog
        else:
            chosen = str(data_dir() / "exports" / name)
        path = Path(chosen)
        if path.suffix.lower() != ".docx":
            path = path.with_suffix(".docx")
        path.parent.mkdir(parents=True, exist_ok=True)
        return {"path": self._helper.tailor_export(int(tailoring_id), accepted_ids or [], path)}

    @_safe
    def set_claude_model(self, model: str | None = None):
        self._helper.set_claude_model(model)

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
