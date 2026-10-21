"""SQLite storage. Everything the app remembers lives in one local file."""
import hashlib
import json
import sqlite3
from pathlib import Path

from jobhelper.paths import db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS resumes (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    filename TEXT,
    raw_text TEXT NOT NULL,
    sections_json TEXT NOT NULL,
    format_flags_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY,
    content_hash TEXT NOT NULL UNIQUE,
    title TEXT, company TEXT, location TEXT, url TEXT,
    raw_text TEXT NOT NULL,
    parsed_json TEXT NOT NULL,
    search_title TEXT,          -- the "Currently searching for" title when imported
    search_level TEXT,          -- entry / mid / senior / lead
    feedback TEXT,              -- liked / applied / interview / rejected (groundwork for v2)
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS vocabulary (
    term TEXT PRIMARY KEY,
    category TEXT NOT NULL DEFAULT 'skill',
    aliases_json TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT 'starter'   -- starter / learned / user
);
CREATE TABLE IF NOT EXISTS scores (
    listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    resume_id INTEGER NOT NULL REFERENCES resumes(id) ON DELETE CASCADE,
    keyword REAL, meaning REAL, coverage REAL, title REAL, level REAL, total REAL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (listing_id, resume_id)
);
CREATE TABLE IF NOT EXISTS embeddings (text_hash TEXT PRIMARY KEY, vec BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    # check_same_thread=False: the window's API calls arrive on worker threads (guarded by a lock in api.py)
    conn = sqlite3.connect(str(path or db_path()), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def content_hash(text: str) -> str:
    normalized = " ".join(text.lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def add_resume(conn, name, filename, raw_text, sections, format_flags=None) -> int:
    cur = conn.execute(
        "INSERT INTO resumes (name, filename, raw_text, sections_json, format_flags_json) VALUES (?,?,?,?,?)",
        (name, filename, raw_text, json.dumps(sections), json.dumps(format_flags or {})),
    )
    conn.commit()
    return cur.lastrowid


def add_listing(conn, raw_text, parsed, url=None, search_title=None, search_level=None):
    """Insert a listing. Returns (id, created); duplicates return the existing id."""
    h = content_hash(raw_text)
    row = conn.execute("SELECT id FROM listings WHERE content_hash = ?", (h,)).fetchone()
    if row:
        return row["id"], False
    cur = conn.execute(
        "INSERT INTO listings (content_hash, title, company, location, url, raw_text, parsed_json,"
        " search_title, search_level) VALUES (?,?,?,?,?,?,?,?,?)",
        (h, parsed.get("title"), parsed.get("company"), parsed.get("location"), url,
         raw_text, json.dumps(parsed), search_title, search_level),
    )
    conn.commit()
    return cur.lastrowid, True
