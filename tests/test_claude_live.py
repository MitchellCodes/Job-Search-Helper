"""One real request to Claude Code (opt-in: it uses your Claude usage and needs `claude` signed in).

Run with:  JOBHELPER_TEST_CLAUDE=1 python -m pytest tests/test_claude_live.py

It sends only the REDACTED text of the fictional sample resume plus a listing, then checks the
properties that matter: nothing personal was sent, every suggestion was checked, nothing invented got
through as valid, and no placeholder is left in what the user would see.
"""
import json
import os
import re
from pathlib import Path

import pytest

from jobhelper.service import JobHelper
from jobhelper.storage import db
from jobhelper.tailoring import claude_cli

pytestmark = pytest.mark.skipif(os.environ.get("JOBHELPER_TEST_CLAUDE") != "1",
                                reason="set JOBHELPER_TEST_CLAUDE=1 to run (uses Claude)")

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "mock_resume_alex_rivera.docx"
LISTING = """Data Analyst
Northwind Analytics
Requirements
- 3+ years of experience with SQL and Tableau
- Experience with dashboards for business stakeholders
- Familiarity with Looker
- Strong communication skills
Nice to have
- Python
"""
PRIVATE = ["Alex Rivera", "alex.rivera@example.com", "(555) 010-0123", "Seattle", "Tacoma", "Brightline",
           "Harbor Freight", "Cascade Community Health", "Cascade State", "Bike Share Demand Forecast"]


def test_real_claude_round_trip(tmp_path):
    assert claude_cli.status()["available"], "Claude Code isn't installed"
    helper = JobHelper(db.connect(tmp_path / "live.sqlite"))
    helper.import_resume(SAMPLE)
    helper.set_search(["Data Analyst"], ["mid"])
    listing_id = helper.import_listing(LISTING)["id"]

    record = helper.tailor_run(listing_id)
    result = record["result"]

    assert not any(p.lower() in record["sent_text"].lower() for p in PRIVATE)       # what was stored as sent
    assert not re.search(r"\[[A-Z_0-9]+\]", json.dumps(result))                     # placeholders all restored
    assert result["meta"]["seconds"] > 0 and result["meta"]["sent_chars"] > 500
    assert all({"id", "valid", "flags"} <= set(e) for e in result["bullet_edits"] + result["skills_to_add"])
    for skill in result["skills_to_add"]:                                           # nothing unsupported is selectable
        if skill["skill"].lower() == "looker":
            assert not skill["valid"]
    for edit in result["bullet_edits"]:
        if edit["valid"]:
            assert edit["original"] in "\n".join(helper.get_resume()["sections"]["experience"])
