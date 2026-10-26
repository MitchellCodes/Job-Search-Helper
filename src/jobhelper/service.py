"""Application logic behind the screens.

Every screen action calls one method here and gets plain dicts/lists back, so the UI layer
stays thin and a different UI (see ROADMAP: Option B) could reuse all of it unchanged.
"""
import json
import sqlite3
from pathlib import Path

from jobhelper.matching.mining import mine_candidates
from jobhelper.matching.profiles import build_profile
from jobhelper.matching.scorer import LEVELS, score_listing
from jobhelper.matching.tfidf import idf_weights
from jobhelper.matching.vocabulary import Vocabulary
from jobhelper.parsing.listing import PARSER_VERSION, parse_listing
from jobhelper.parsing.resume import estimate_years_experience, parse_resume_file, split_sections
from jobhelper.privacy.redact import redact, render_sections
from jobhelper.storage import db
from jobhelper.tailoring import claude_cli
from jobhelper.tailoring.docx_export import build_docx, build_structure, to_text
from jobhelper.tailoring.tailor import PrivacyBlocked, tailor


class JobHelper:
    def __init__(self, conn: sqlite3.Connection | None = None, embedder=None, claude_runner=None):
        self._claude_runner = claude_runner      # replaced by a fake in tests
        self.conn = conn or db.connect()
        self._embedder = embedder          # lazily created so the window opens quickly
        self._embedder_failed = False
        self.vocab = Vocabulary.load(self.conn)
        self._reparse_if_parser_changed()

    def _reparse_if_parser_changed(self) -> int:
        """After an app update that improves listing parsing, refresh saved listings from their text."""
        if self.get_setting("parser_version") == PARSER_VERSION:
            return 0
        rows = self.conn.execute("SELECT id, raw_text, url FROM listings").fetchall()
        for r in rows:
            parsed = parse_listing(r["raw_text"], r["url"])
            self.conn.execute("UPDATE listings SET title=?, company=?, location=?, parsed_json=? WHERE id=?",
                              (parsed["title"], parsed["company"], parsed["location"], json.dumps(parsed), r["id"]))
        self.conn.execute("DELETE FROM scores")
        self.conn.commit()
        self.set_setting("parser_version", PARSER_VERSION)
        return len(rows)

    # -- settings ------------------------------------------------------------------
    def get_setting(self, key: str, default=None):
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value) -> None:
        self.conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                          "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, json.dumps(value)))
        self.conn.commit()

    def get_search(self) -> dict:
        """The 'Currently searching for' context: titles and experience levels."""
        return {"titles": self.get_setting("search_titles", []),
                "levels": self.get_setting("search_levels", [])}

    def set_search(self, titles: list[str], levels: list[str]) -> dict:
        titles = [t.strip() for t in titles if t and t.strip()]
        bad = [l for l in levels if l not in LEVELS]
        if bad:
            raise ValueError(f"Unknown experience level(s): {bad}; use {LEVELS}")
        self.set_setting("search_titles", titles)
        self.set_setting("search_levels", levels)
        return self.get_search()

    # -- embedder ------------------------------------------------------------------
    @property
    def embedder(self):
        """Cached embedding model, or None if it can't load (keyword scoring still works)."""
        if self._embedder is None and not self._embedder_failed:
            try:
                from jobhelper.matching.embeddings import CachedEmbedder, Embedder

                self._embedder = CachedEmbedder(Embedder(), self.conn)
            except Exception:  # noqa: BLE001 - missing/corrupt model must not break the app
                self._embedder_failed = True
        return self._embedder

    # -- resumes -------------------------------------------------------------------
    def import_resume(self, path: str | Path, name: str | None = None) -> int:
        path = Path(path)
        parsed = parse_resume_file(path)
        return db.add_resume(self.conn, name or path.stem, path.name, parsed["raw_text"],
                             parsed["sections"], parsed["format_flags"])

    def list_resumes(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT id, name, filename, created_at FROM resumes ORDER BY id DESC")]

    def get_resume(self, resume_id: int | None = None) -> dict | None:
        row = (self.conn.execute("SELECT * FROM resumes WHERE id = ?", (resume_id,)).fetchone()
               if resume_id else self.conn.execute("SELECT * FROM resumes ORDER BY id DESC LIMIT 1").fetchone())
        if not row:
            return None
        sections = json.loads(row["sections_json"])
        from jobhelper.parsing.resume import extract_contact, extract_skill_items

        return {"id": row["id"], "name": row["name"], "filename": row["filename"],
                "raw_text": row["raw_text"], "sections": sections,
                "contact": extract_contact(row["raw_text"]),
                "skills_listed": extract_skill_items(sections),
                "format_flags": json.loads(row["format_flags_json"]),
                "years_experience": estimate_years_experience(sections)}

    def update_resume_text(self, resume_id: int, text: str) -> None:
        """Let the user correct a mis-parsed resume; sections are re-derived from the text."""
        self.conn.execute("UPDATE resumes SET raw_text = ?, sections_json = ? WHERE id = ?",
                          (text, json.dumps(split_sections(text)), resume_id))
        self.conn.execute("DELETE FROM scores WHERE resume_id = ?", (resume_id,))
        self.conn.commit()

    # -- listings ------------------------------------------------------------------
    def import_listing(self, text: str, url: str | None = None) -> dict:
        """Add a listing the user pasted/copied, tagged with the current search context."""
        text = (text or "").strip()
        if len(text) < 40:
            raise ValueError("That looks too short to be a job posting.")
        search = self.get_search()
        parsed = parse_listing(text, url)
        title = search["titles"][0] if len(search["titles"]) == 1 else None
        level = search["levels"][0] if len(search["levels"]) == 1 else None
        listing_id, created = db.add_listing(self.conn, text, parsed, url=url,
                                             search_title=title, search_level=level)
        return {"id": listing_id, "created": created, "title": parsed["title"], "company": parsed["company"]}

    def import_listings_bulk(self, text: str, separator: str = "====") -> list[dict]:
        chunks = [c.strip() for c in text.split(separator) if len(c.strip()) >= 40]
        return [self.import_listing(c) for c in chunks]

    def set_listing_tags(self, listing_id: int, search_title: str | None, search_level: str | None) -> None:
        self.conn.execute("UPDATE listings SET search_title = ?, search_level = ? WHERE id = ?",
                          (search_title, search_level, listing_id))
        self.conn.commit()

    def set_feedback(self, listing_id: int, feedback: str | None) -> None:
        allowed = {None, "liked", "applied", "interview", "rejected"}
        if feedback not in allowed:
            raise ValueError(f"feedback must be one of {sorted(x for x in allowed if x)}")
        self.conn.execute("UPDATE listings SET feedback = ? WHERE id = ?", (feedback, listing_id))
        self.conn.commit()

    def delete_listing(self, listing_id: int) -> None:
        self.conn.execute("DELETE FROM listings WHERE id = ?", (listing_id,))
        self.conn.commit()

    def get_listing(self, listing_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["parsed"] = json.loads(d.pop("parsed_json"))
        return d

    def _all_parsed(self, where: str = "", args=()) -> list[dict]:
        rows = self.conn.execute(f"SELECT id, parsed_json FROM listings {where}", args).fetchall()
        out = []
        for r in rows:
            parsed = json.loads(r["parsed_json"])
            parsed["_id"] = r["id"]
            out.append(parsed)
        return out

    def _idf(self) -> dict:
        return idf_weights([set(self.vocab.find(" ".join(p.get("requirements", []) or
                                                             p.get("sections", {}).get("intro", []))))
                            for p in self._all_parsed()])

    # -- scoring -------------------------------------------------------------------
    def score(self, listing_id: int, resume_id: int | None = None, use_meaning: bool = True) -> dict:
        resume = self.get_resume(resume_id)
        listing = self.get_listing(listing_id)
        if resume is None:
            raise ValueError("Upload a resume first.")
        if listing is None:
            raise ValueError(f"No listing with id {listing_id}.")
        search = self.get_search()
        result = score_listing(resume, listing["parsed"], self.vocab, self._idf(),
                               embedder=self.embedder if use_meaning else None,
                               target_titles=search["titles"], wanted_levels=search["levels"],
                               resume_years=resume["years_experience"])
        c = result["components"]
        self.conn.execute(
            "INSERT OR REPLACE INTO scores (listing_id, resume_id, keyword, meaning, coverage, title, level, total, detail_json)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (listing_id, resume["id"], c["keyword"], c["meaning"], c["coverage"], c["title"], c["level"],
             result["total"], json.dumps(result)))
        self.conn.commit()
        return result

    def ranked_listings(self, resume_id: int | None = None, min_score: float = 0, level: str | None = None,
                        exclude_words: list[str] | None = None, remote: str | None = None) -> list[dict]:
        """Best fits first. Scores are (re)computed so rankings reflect the current vocabulary."""
        resume = self.get_resume(resume_id)
        if resume is None:
            return []
        rows = []
        for r in self.conn.execute("SELECT id FROM listings"):
            listing = self.get_listing(r["id"])
            p = listing["parsed"]
            if level and p.get("level") != level:
                continue
            if remote and p.get("remote") != remote:
                continue
            blob = f"{listing['title']} {listing['company']}".lower()
            if exclude_words and any(w.lower() in blob for w in exclude_words):
                continue
            res = self.score(listing["id"], resume["id"])
            if (res["total"] or 0) < min_score:
                continue
            rows.append({"id": listing["id"], "title": listing["title"], "company": listing["company"],
                         "level": p.get("level"), "remote": p.get("remote"), "total": res["total"],
                         "coverage_percent": res["coverage"]["percent"], "feedback": listing["feedback"],
                         "search_title": listing["search_title"], "search_level": listing["search_level"]})
        return sorted(rows, key=lambda x: (x["total"] is None, -(x["total"] or 0)))

    # -- vocabulary learning ---------------------------------------------------------
    def new_term_candidates(self, **kw) -> list[dict]:
        ignored = set(self.get_setting("ignored_terms", []))
        return [c for c in mine_candidates(self._all_parsed(), self.vocab, **kw) if c["term"] not in ignored]

    def ignore_term(self, term: str) -> None:
        ignored = set(self.get_setting("ignored_terms", []))
        ignored.add(term)
        self.set_setting("ignored_terms", sorted(ignored))

    def accept_term(self, term: str, category: str = "skill", merge_into: str | None = None) -> None:
        """Add a mined term as a new skill, or as an alias of an existing one."""
        if merge_into:
            if merge_into not in self.vocab.terms:
                raise ValueError(f"Unknown skill: {merge_into}")
            self.vocab.add_alias(merge_into, term)
            self.vocab.terms[merge_into]["source"] = self.vocab.terms[merge_into].get("source", "user")
            self.vocab.save_term(self.conn, merge_into)
        else:
            display = " ".join(w.upper() if len(w) <= 3 else w.capitalize() for w in term.split())
            self.vocab.add(display, category, [term] if term != display.lower() else [], source="learned")
            self.vocab.save_term(self.conn, display)

    # -- role profiles ---------------------------------------------------------------
    def role_profile(self, title: str, level: str | None = None, resume_id: int | None = None) -> dict:
        args: list = [title]
        where = "WHERE search_title = ? COLLATE NOCASE"
        if level:
            where += " AND search_level = ?"
            args.append(level)
        listings = self._all_parsed(where, args)
        resume = self.get_resume(resume_id)
        resume_terms = set(self.vocab.find(resume["raw_text"])) if resume else None
        profile = build_profile(listings, self.vocab, resume_terms)
        profile.update({"title": title, "level": level})
        return profile

    def role_options(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT search_title AS title, search_level AS level, COUNT(*) AS listings FROM listings "
            "WHERE search_title IS NOT NULL GROUP BY search_title, search_level ORDER BY listings DESC")]

    # -- tailoring with Claude -------------------------------------------------------------------
    def hide_terms(self) -> list[str]:
        return self.get_setting("hide_terms", [])

    def claude_model(self) -> str | None:
        return self.get_setting("claude_model") or None

    def set_claude_model(self, model: str | None) -> None:
        self.set_setting("claude_model", (model or "").strip())

    def _tailor_inputs(self, listing_id: int, resume_id: int | None):
        resume = self.get_resume(resume_id)
        if resume is None:
            raise ValueError("Upload a resume first.")
        listing = self.get_listing(int(listing_id))
        if listing is None:
            raise ValueError("That listing no longer exists.")
        return resume, listing

    def tailor_preview(self, listing_id: int, resume_id: int | None = None, extra_terms=None) -> dict:
        """Show exactly what would be sent to Claude, without sending anything."""
        resume, listing = self._tailor_inputs(listing_id, resume_id)
        if extra_terms is not None:
            self.set_setting("hide_terms", [t.strip() for t in extra_terms if t and t.strip()])
        red = redact(resume, self.hide_terms())
        return {"items": [{"placeholder": i.placeholder, "original": i.original, "kind": i.kind, "count": i.count}
                          for i in red.items],
                "warnings": red.warnings, "leaks": red.leaks, "hide_terms": self.hide_terms(),
                "resume_text": render_sections(red), "listing_text": listing["raw_text"],
                "claude": claude_cli.status(), "model": self.claude_model()}

    # The three steps of a tailoring run are separate so the app can release its lock while Claude works.
    def tailor_prepare(self, listing_id: int, resume_id: int | None = None, extra_terms=None) -> dict:
        resume, listing = self._tailor_inputs(listing_id, resume_id)
        if extra_terms is not None:
            self.set_setting("hide_terms", [t.strip() for t in extra_terms if t and t.strip()])
        red = redact(resume, self.hide_terms())
        if red.leaks:                                    # refuse before anything is built or sent
            raise ValueError(str(PrivacyBlocked(red.leaks)))
        score = self.score(int(listing_id), resume["id"])
        return {"listing": listing, "score": score, "redaction": red, "resume_id": resume["id"],
                "model": self.claude_model()}

    def tailor_call(self, job: dict) -> dict:
        """Runs Claude. Touches no database, so it can run while the app serves other requests."""
        try:
            return tailor(job["listing"], job["score"], job["redaction"], model=job["model"],
                          runner=self._claude_runner or claude_cli.run_claude)
        except (PrivacyBlocked, claude_cli.ClaudeUnavailable, claude_cli.ClaudeFailed) as e:
            raise ValueError(str(e)) from e

    def tailor_finish(self, job: dict, result: dict) -> dict:
        cur = self.conn.execute(
            "INSERT INTO tailorings (listing_id, resume_id, result_json, sent_text) VALUES (?,?,?,?)",
            (job["listing"]["id"], job["resume_id"], json.dumps(result), render_sections(job["redaction"])))
        self.conn.commit()
        return self.tailor_get(cur.lastrowid)

    def tailor_run(self, listing_id: int, resume_id: int | None = None, extra_terms=None) -> dict:
        job = self.tailor_prepare(listing_id, resume_id, extra_terms)
        return self.tailor_finish(job, self.tailor_call(job))

    def tailor_get(self, tailoring_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM tailorings WHERE id = ?", (tailoring_id,)).fetchone()
        if not row:
            return None
        return {"id": row["id"], "listing_id": row["listing_id"], "resume_id": row["resume_id"],
                "created_at": row["created_at"], "result": json.loads(row["result_json"]),
                "sent_text": row["sent_text"]}

    def tailor_latest(self, listing_id: int) -> dict | None:
        row = self.conn.execute("SELECT id FROM tailorings WHERE listing_id = ? ORDER BY id DESC LIMIT 1",
                                (int(listing_id),)).fetchone()
        return self.tailor_get(row["id"]) if row else None

    def _tailored_structure(self, tailoring_id: int, accepted_ids):
        record = self.tailor_get(int(tailoring_id))
        resume = self.get_resume(record["resume_id"]) if record else None
        if record is None or resume is None:
            raise ValueError("That tailoring result (or its resume) no longer exists.")
        return build_structure(resume, record["result"], set(accepted_ids or []))

    def tailor_text(self, tailoring_id: int, accepted_ids) -> str:
        return to_text(self._tailored_structure(tailoring_id, accepted_ids))

    def tailor_export(self, tailoring_id: int, accepted_ids, path: str | Path) -> str:
        return str(build_docx(self._tailored_structure(tailoring_id, accepted_ids), path))

    # -- data management ---------------------------------------------------------------
    def delete_all_data(self) -> None:
        for table in ("tailorings", "scores", "listings", "resumes", "vocabulary", "embeddings", "settings"):
            self.conn.execute(f"DELETE FROM {table}")
        self.conn.commit()
        self.vocab = Vocabulary.load(self.conn)
