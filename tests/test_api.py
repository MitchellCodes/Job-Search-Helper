import json
import re
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from jobhelper.api import PUBLIC_METHODS, Api
from jobhelper.devserver import make_handler
from jobhelper.parsing.resume import parse_resume_file
from jobhelper.privacy.redact import Redaction
from jobhelper.service import JobHelper
from jobhelper.storage import db
from test_matching import ANALYST

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "mock_resume_alex_rivera.docx"
BULLET = ("Built and maintain 8 Tableau dashboards used by 12 store managers and 3 regional directors, "
          "replacing manual spreadsheet reports and saving about 6 hours of work per week")

REPLY = {
    "summary": {"suggested": "", "reason": ""},
    "skills_to_add": [],
    "bullet_edits": [{"original": BULLET,
                      "suggested": BULLET.replace("Tableau dashboards", "Tableau dashboards for business stakeholders"),
                      "keywords_added": [], "reason": "mirrors the listing"}],
    "gaps_not_to_claim": [{"skill": "Looker", "advice": "Learn it first."}],
    "notes": "ok",
}


class ScriptedClaude:
    """Stands in for Claude: records exactly what it was sent."""

    def __init__(self, reply=REPLY):
        self.reply, self.calls = reply, []

    def __call__(self, user, schema, system, model=None, timeout=0):
        self.calls.append({"user": user, "system": system, "model": model})
        return {"data": self.reply, "seconds": 2.0, "cost_usd": 0.01, "model": "scripted"}


@pytest.fixture()
def claude():
    return ScriptedClaude()


@pytest.fixture()
def api(tmp_path, claude):
    saved = tmp_path / "saved.docx"
    helper = JobHelper(db.connect(tmp_path / "a.sqlite"), claude_runner=claude)
    a = Api(helper, file_picker=lambda: str(SAMPLE), file_saver=lambda name: str(saved))
    a._saved = saved
    return a


@pytest.fixture()
def ready(api):
    """A resume uploaded, a search set and one listing imported."""
    assert api.choose_resume()["ok"]
    api.set_search(["Data Analyst"], ["entry"])
    return api.import_text(ANALYST)["data"]["id"]


def test_every_method_returns_envelope_and_errors_are_caught(api):
    bad = api.import_text("short")
    assert bad["ok"] is False and "too short" in bad["error"]
    assert api.get_resume() == {"ok": True, "data": None}
    assert api.delete_all_data()["ok"] is False                      # needs confirm=True
    assert api.listing_detail(999)["ok"] is False
    assert api.tailor_run(999)["ok"] is False


def test_private_state_and_removed_features_are_not_exposed(api):
    assert not any(n.startswith("_") for n in PUBLIC_METHODS)
    assert {"state", "ranked", "choose_resume", "tailor_preview", "tailor_run", "tailor_export"} <= PUBLIC_METHODS
    assert not {"import_clipboard", "browser_open", "browser_status", "copy_text", "search_links"} & PUBLIC_METHODS


def test_end_to_end_flow_without_claude(api):
    state = api.state()["data"]
    assert state["resume"] is None and set(state["claude"]) == {"available", "version"}
    assert api.choose_resume()["ok"] and api.state()["data"]["resume"]["name"] == "mock_resume_alex_rivera"
    assert api.set_search(["Data Analyst"], ["entry"])["ok"]
    imported = api.import_text(ANALYST, "https://careers.example.com/analyst-7")["data"]
    assert imported["created"] and imported["title"] == "Junior Data Analyst"
    detail = api.listing_detail(imported["id"])["data"]
    assert detail["listing"]["url"] == "https://careers.example.com/analyst-7"
    assert detail["score"]["coverage"]["total"] >= 3
    ranked = api.ranked()["data"]
    assert len(ranked) == 1 and ranked[0]["id"] == imported["id"]
    assert api.set_feedback(imported["id"], "applied")["ok"]
    assert api.role_profile("Data Analyst", "entry")["data"]["listing_count"] == 1
    bulk = api.import_bulk(ANALYST.replace("Acme", "Other") + "\n====\n" + ANALYST.replace("Acme", "Third"))["data"]
    assert len(bulk) == 2 and all(b["created"] for b in bulk)


def test_ignore_term_persists(api):
    api.set_search(["Data Analyst"], ["entry"])
    for i in range(2):
        api.import_text(f"Analyst {i}\nCo\nRequirements\n- Experience with Looker Studio dashboards\n- SQL knowledge")
    assert "looker studio" in [c["term"] for c in api.term_candidates()["data"]]
    api.ignore_term("looker studio")
    assert "looker studio" not in [c["term"] for c in api.term_candidates()["data"]]


# ---- tailoring through the API ------------------------------------------------------------------------
def test_preview_shows_what_would_be_sent_and_sends_nothing(api, ready, claude):
    out = api.tailor_preview(ready, extra_terms=["Confluence"])["data"]
    kinds = {i["kind"] for i in out["items"]}
    assert {"EMAIL", "PHONE", "LOCATION", "NAME", "EMPLOYER", "SCHOOL", "PROJECT", "CERT", "HIDDEN"} <= kinds
    assert out["leaks"] == [] and "[EMPLOYER_1]" in out["resume_text"] and "Confluence" not in out["resume_text"]
    assert out["listing_text"].startswith("Junior Data Analyst") and "alex.rivera" not in out["resume_text"]
    assert out["hide_terms"] == ["Confluence"]                                   # remembered for next time
    assert api.tailor_preview(ready)["data"]["hide_terms"] == ["Confluence"]
    assert claude.calls == []


def test_run_sends_only_redacted_text_and_saves_a_checked_result(api, ready, claude):
    out = api.tailor_run(ready)
    assert out["ok"], out
    sent = claude.calls[0]["user"] + claude.calls[0]["system"]
    for secret in ("Alex Rivera", "alex.rivera@example.com", "(555) 010-0123", "Seattle", "Brightline",
                   "Cascade State", "Bike Share"):
        assert secret.lower() not in sent.lower()
    assert "JOB LISTING" in sent and "[EMPLOYER_1]" in sent
    record = out["data"]
    edit = record["result"]["bullet_edits"][0]
    assert edit["valid"] and "for business stakeholders" in edit["suggested"]
    assert not re.search(r"\[[A-Z_0-9]+\]", json.dumps(record["result"]))      # no placeholder left unrestored
    assert "Brightline" not in record["sent_text"] and "[EMPLOYER_1]" in record["sent_text"]   # audit trail is redacted
    latest = api.tailor_latest(ready)["data"]
    assert latest["id"] == record["id"]
    assert api.tailor_latest(99999)["data"] is None


def test_model_setting_is_passed_to_claude(api, ready, claude):
    api.set_claude_model("some-model")
    api.tailor_run(ready)
    assert claude.calls[0]["model"] == "some-model"
    assert api.state()["data"]["claude_model"] == "some-model"


def test_export_writes_a_word_file_with_real_details_and_only_accepted_edits(api, ready):
    record = api.tailor_run(ready)["data"]
    edit_id = record["result"]["bullet_edits"][0]["id"]
    out = api.tailor_export(record["id"], [edit_id])
    assert out["ok"] and out["data"]["path"] == str(api._saved)
    again = parse_resume_file(api._saved)
    assert again["contact"]["email"] == "alex.rivera@example.com"
    assert any("for business stakeholders" in l for l in again["sections"]["experience"])
    text = api.tailor_text(record["id"], [edit_id])["data"]
    assert "Alex Rivera" in text and "Brightline Retail Group" in text and "for business stakeholders" in text

    api.tailor_export(record["id"], [])                                           # nothing accepted: original wording
    assert not any("for business stakeholders" in l for l in parse_resume_file(api._saved)["sections"]["experience"])


def test_export_cancelled_dialog_and_default_location(tmp_path, ready, api, monkeypatch):
    record = api.tailor_run(ready)["data"]
    api._file_saver = lambda name: None
    assert api.tailor_export(record["id"], [])["data"] is None                    # cancelled: nothing written
    monkeypatch.setenv("JOBHELPER_DATA", str(tmp_path / "datadir"))
    api._file_saver = None
    out = api.tailor_export(record["id"], [])["data"]["path"]
    assert Path(out).parent.name == "exports" and Path(out).name.startswith("Tailored resume - ")
    assert Path(out).suffix == ".docx" and Path(out).exists()


def test_privacy_block_stops_everything_before_claude_is_called(api, ready, claude, monkeypatch):
    import jobhelper.service as service

    real = service.redact

    def leaky(resume, extra_terms=()):
        red = real(resume, extra_terms)
        red.leaks = ["email still present: someone@example.com"]
        return red

    monkeypatch.setattr(service, "redact", leaky)
    out = api.tailor_run(ready)
    assert out["ok"] is False and "Not sent" in out["error"] and "someone@example.com" in out["error"]
    assert claude.calls == []
    assert api.tailor_latest(ready)["data"] is None                               # nothing saved either


def test_claude_errors_reach_the_user_as_messages(tmp_path):
    from jobhelper.tailoring.claude_cli import ClaudeFailed, ClaudeUnavailable

    for exc, text in ((ClaudeFailed("Claude took longer than 300 seconds. Try again."), "took longer"),
                      (ClaudeUnavailable("Claude Code wasn't found."), "wasn't found")):
        def boom(*a, **k):
            raise exc

        a = Api(JobHelper(db.connect(tmp_path / f"{text[:4]}.sqlite"), claude_runner=boom),
                file_picker=lambda: str(SAMPLE))
        a.choose_resume()
        a.set_search(["Data Analyst"], ["entry"])
        lid = a.import_text(ANALYST)["data"]["id"]
        out = a.tailor_run(lid)
        assert out["ok"] is False and text in out["error"]


def test_the_app_stays_responsive_while_claude_works(api, ready):
    started, release = threading.Event(), threading.Event()

    def slow(user, schema, system, model=None, timeout=0):
        started.set()
        assert release.wait(20)
        return {"data": REPLY, "seconds": 1, "cost_usd": None, "model": "slow"}

    api._helper._claude_runner = slow
    result = {}
    worker = threading.Thread(target=lambda: result.update(api.tailor_run(ready)))
    worker.start()
    assert started.wait(20)
    answered = threading.Event()
    threading.Thread(target=lambda: (api.state(), answered.set())).start()
    assert answered.wait(5), "other requests were blocked while Claude was working"
    release.set()
    worker.join(20)
    assert result["ok"] is True


def test_resume_required_before_tailoring(tmp_path):
    a = Api(JobHelper(db.connect(tmp_path / "n.sqlite")))
    a.set_search(["Data Analyst"], ["entry"])
    lid = a.import_text(ANALYST)["data"]["id"]
    assert "resume" in a.tailor_run(lid)["error"].lower()


def test_dev_server_serves_pages_and_api(api):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(api))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert b"Job Search" in urllib.request.urlopen(base + "/").read()
        assert b"function ring" in urllib.request.urlopen(base + "/app.js").read()
        req = urllib.request.Request(base + "/api/state", data=b"[]", method="POST")
        assert json.loads(urllib.request.urlopen(req).read())["ok"] is True
        for bad in ("/api/_helper", "/api/__init__"):
            with pytest.raises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(urllib.request.Request(base + bad, data=b"[]", method="POST"))
            assert e.value.code == 404
        with pytest.raises(urllib.error.HTTPError) as e:           # path traversal
            urllib.request.urlopen(base + "/../api.py")
        assert e.value.code == 404
    finally:
        server.shutdown()
