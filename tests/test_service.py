import pytest

from jobhelper.matching.embeddings import MODEL_DIR
from jobhelper.service import JobHelper
from jobhelper.storage import db
from test_matching import ANALYST, DEVOPS, RESUME_TEXT
from test_parsing_storage import make_docx, paragraphs


@pytest.fixture()
def app(tmp_path):
    conn = db.connect(tmp_path / "app.sqlite")
    helper = JobHelper(conn)
    docx = tmp_path / "resume.docx"
    make_docx(docx, paragraphs(RESUME_TEXT.splitlines()))
    helper.import_resume(docx, "Main")
    return helper


def test_resume_roundtrip_and_years(app):
    r = app.get_resume()
    assert r["name"] == "Main" and r["contact"]["email"] == "jane@example.com"
    assert "Python" in r["skills_listed"]
    assert r["years_experience"] is not None and r["years_experience"] >= 4


def test_search_context_tags_imported_listings(app):
    app.set_search(["Data Analyst"], ["entry"])
    out = app.import_listing(ANALYST)
    assert out["created"] is True
    row = app.get_listing(out["id"])
    assert (row["search_title"], row["search_level"]) == ("Data Analyst", "entry")
    assert app.import_listing(ANALYST)["created"] is False        # duplicate
    assert app.role_options() == [{"title": "Data Analyst", "level": "entry", "listings": 1}]


def test_input_validation(app):
    with pytest.raises(ValueError):
        app.import_listing("too short")
    with pytest.raises(ValueError):
        app.set_search(["x"], ["wizard"])
    with pytest.raises(ValueError):
        app.set_feedback(1, "maybe")


def test_score_and_ranking_keyword_only(app):
    app.set_search(["Data Analyst"], ["entry"])
    a = app.import_listing(ANALYST)["id"]
    d = app.import_listing(DEVOPS)["id"]
    ranked = app.ranked_listings()
    assert [r["id"] for r in ranked] == [a, d]
    assert ranked[0]["total"] > ranked[1]["total"]
    assert app.ranked_listings(min_score=50) and all(r["total"] >= 50 for r in app.ranked_listings(min_score=50))
    assert [r["id"] for r in app.ranked_listings(level="senior")] == [d]
    assert app.ranked_listings(exclude_words=["devops"]) [0]["id"] == a
    saved = app.conn.execute("SELECT total, detail_json FROM scores WHERE listing_id = ?", (a,)).fetchone()
    assert saved["total"] == ranked[0]["total"]


@pytest.mark.skipif(not (MODEL_DIR / "model_quantized.onnx").exists(), reason="model missing")
def test_score_with_meaning_layer_uses_cache(app):
    app.set_search(["Data Analyst"], ["entry"])
    lid = app.import_listing(ANALYST)["id"]
    res = app.score(lid)
    assert res["components"]["meaning"] is not None
    cached = app.conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    assert cached > 0
    app.score(lid)
    assert app.conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0] == cached


def test_learning_terms_changes_future_matching(app):
    app.set_search(["Data Analyst"], ["entry"])
    for i in range(2):
        app.import_listing(f"Analyst {i}\nCo\nRequirements\n- Experience with Looker Studio dashboards\n- SQL knowledge")
    terms = [c["term"] for c in app.new_term_candidates()]
    assert "looker studio" in terms
    assert "Looker Studio" not in app.vocab.find("We use Looker Studio")      # not known yet
    app.accept_term("looker studio", "tool")
    assert "Looker Studio" in app.vocab.find("We use Looker Studio")
    reloaded = JobHelper(app.conn)                      # persisted
    assert "Looker Studio" in reloaded.vocab.find("We use Looker Studio")
    leftovers = [c["term"] for c in app.new_term_candidates()]
    assert "looker studio" not in leftovers
    assert "studio" not in leftovers and "looker studio dashboards" not in leftovers   # fragments/extensions


def test_merge_term_as_alias(app):
    app.accept_term("power bi desktop", merge_into="Power BI")
    assert "Power BI" in app.vocab.find("Experience with Power BI Desktop")
    assert "Power BI" in JobHelper(app.conn).vocab.find("Power BI Desktop")


def test_role_profile_and_readiness(app):
    app.set_search(["Data Analyst"], ["entry"])
    app.import_listing(ANALYST)
    app.import_listing(ANALYST.replace("Power BI", "Looker").replace("Junior", "Entry-level"))
    app.set_search(["Data Engineer"], ["senior"])
    app.import_listing(DEVOPS)
    p = app.role_profile("Data Analyst", "entry")
    assert p["listing_count"] == 2 and p["confidence"] == "low"
    assert {r["term"]: r["percent"] for r in p["top_required"]}["SQL"] == 100
    assert p["readiness_percent"] is not None
    assert app.role_profile("data analyst", None)["listing_count"] == 2      # case-insensitive
    assert app.role_profile("Data Engineer", "senior")["listing_count"] == 1


def test_delete_all_data(app):
    app.set_search(["Data Analyst"], ["entry"])
    app.import_listing(ANALYST)
    app.delete_all_data()
    assert app.get_resume() is None and app.ranked_listings() == [] and app.get_search() == {"titles": [], "levels": []}


def test_saved_listings_are_reparsed_when_parser_version_changes(app):
    lid = app.import_listing("Data Analyst I\nCo\nRequirements\n- SQL experience required daily")["id"]
    # simulate a listing saved by an older parser: stale parsed data and an old version number
    app.conn.execute("UPDATE listings SET parsed_json = '{\"title\": \"stale\", \"requirements\": []}' WHERE id = ?", (lid,))
    app.set_setting("parser_version", 0)
    again = JobHelper(app.conn)
    assert again.get_listing(lid)["parsed"]["level"] == "entry"
    assert again.get_listing(lid)["title"] == "Data Analyst I"
    assert again.get_setting("parser_version") >= 2
