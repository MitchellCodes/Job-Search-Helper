import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from jobhelper.api import PUBLIC_METHODS, Api
from jobhelper.devserver import make_handler
from jobhelper.service import JobHelper
from jobhelper.storage import db
from test_matching import ANALYST, RESUME_TEXT
from test_parsing_storage import make_docx, paragraphs


class FakeBrowser:
    def __init__(self):
        self.opened, self.actions = [], []

    def open(self, url):
        self.opened.append(url)

    def current_url(self):
        return "https://example.com/jobs/1"

    def back(self):
        self.actions.append("back")


@pytest.fixture()
def api(tmp_path):
    docx = tmp_path / "resume.docx"
    make_docx(docx, paragraphs(RESUME_TEXT.splitlines()))
    a = Api(JobHelper(db.connect(tmp_path / "a.sqlite")), browser=FakeBrowser(),
            clipboard=lambda: ANALYST, file_picker=lambda: str(docx))
    return a


def test_every_method_returns_envelope_and_errors_are_caught(api):
    bad = api.import_text("short")
    assert bad["ok"] is False and "too short" in bad["error"]
    assert api.get_resume() == {"ok": True, "data": None}
    assert api.delete_all_data()["ok"] is False                      # needs confirm=True
    assert api.listing_detail(999)["ok"] is False


def test_private_state_is_not_exposed(api):
    assert not any(n.startswith("_") for n in PUBLIC_METHODS)
    assert {"state", "ranked", "import_clipboard", "choose_resume"} <= PUBLIC_METHODS


def test_end_to_end_flow(api):
    assert api.state()["data"]["resume"] is None
    assert api.choose_resume()["ok"]
    assert api.state()["data"]["resume"]["name"] == "resume"
    assert api.set_search(["Data Analyst"], ["entry"])["ok"]
    links = api.search_links("Seattle, WA")["data"]["Data Analyst"]
    assert set(links) == {"LinkedIn", "Indeed", "ZipRecruiter", "Glassdoor"}
    assert "entry+level+Data+Analyst" in links["Indeed"] and "Seattle%2C+WA" in links["Indeed"]
    imported = api.import_clipboard()["data"]
    assert imported["created"] and imported["title"] == "Junior Data Analyst"
    listing = api.listing_detail(imported["id"])["data"]
    assert listing["listing"]["url"] == "https://example.com/jobs/1"      # address of the page the user was on
    assert listing["score"]["coverage"]["total"] >= 3
    ranked = api.ranked()["data"]
    assert len(ranked) == 1 and ranked[0]["id"] == imported["id"]
    assert api.set_feedback(imported["id"], "applied")["ok"]
    assert api.ranked()["data"][0]["feedback"] == "applied"
    assert api.role_profile("Data Analyst", "entry")["data"]["listing_count"] == 1
    assert api.browser_open("indeed.com")["ok"] and api._browser.opened == ["indeed.com"]
    assert api.browser_action("back")["ok"] and api._browser.actions == ["back"]
    assert api.browser_action("explode")["ok"] is False


def test_empty_clipboard_gives_helpful_message(tmp_path):
    a = Api(JobHelper(db.connect(tmp_path / "b.sqlite")), clipboard=lambda: "  ", browser=FakeBrowser())
    out = a.import_clipboard()
    assert out["ok"] is False and "Ctrl+C" in out["error"]


def test_ignore_term_persists(api):
    api.set_search(["Data Analyst"], ["entry"])
    for i in range(2):
        api.import_text(f"Analyst {i}\nCo\nRequirements\n- Experience with Looker Studio dashboards\n- SQL knowledge")
    assert "looker studio" in [c["term"] for c in api.term_candidates()["data"]]
    api.ignore_term("looker studio")
    assert "looker studio" not in [c["term"] for c in api.term_candidates()["data"]]


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
