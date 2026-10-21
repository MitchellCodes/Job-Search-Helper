import pytest

from jobhelper.matching.embeddings import CachedEmbedder, Embedder, MODEL_DIR
from jobhelper.matching.mining import mine_candidates
from jobhelper.matching.profiles import build_profile
from jobhelper.matching.scorer import score_listing
from jobhelper.matching.tfidf import idf_weights
from jobhelper.matching.vocabulary import Vocabulary
from jobhelper.parsing.listing import parse_listing
from jobhelper.parsing.resume import (
    estimate_years_experience, extract_skill_items, split_sections,
)
from jobhelper.storage import db

RESUME_TEXT = """Jane Doe
jane@example.com

Summary
Data analyst who builds reporting dashboards and automates data cleaning.

Skills
Languages: Python, SQL
Tools: Tableau, Excel, Git

Experience
Data Analyst, Acme Corp  Jan 2022 - Present
- Built Tableau dashboards that cut weekly reporting time by 40%
- Wrote SQL queries against PostgreSQL to analyze customer churn
- Presented findings to non-technical stakeholders each month

Education
B.S. Statistics, University of Washington
"""

ANALYST = """Junior Data Analyst
Acme Analytics
Requirements
- 1+ years of experience with SQL
- Proficiency in Tableau or Power BI
- Strong communication skills
Nice to have
- Python
"""

DEVOPS = """Senior DevOps Engineer
CloudCo
Requirements
- 7+ years of experience with Kubernetes and Terraform
- Deep AWS expertise
- Experience with Jenkins CI/CD pipelines
Nice to have
- Ansible
"""


@pytest.fixture(scope="module")
def vocab():
    return Vocabulary.load()


@pytest.fixture(scope="module")
def embedder():
    if not (MODEL_DIR / "model_quantized.onnx").exists():
        pytest.skip("bundled model not present")
    return Embedder()


def resume_dict():
    sections = split_sections(RESUME_TEXT)
    return {"raw_text": RESUME_TEXT, "sections": sections, "skills_listed": extract_skill_items(sections)}


# ---- vocabulary ---------------------------------------------------------------
def test_vocab_whole_word_and_aliases(vocab):
    found = vocab.find("We use JS and C++ plus Java, not JavaScript-only. Node.js, C#, R and .NET too.")
    assert {"JavaScript", "C++", "Java", "Node.js", "C#", "R", ".NET"} <= set(found)
    assert "C" not in found                     # "C++" / "C#" must not count as C
    assert vocab.find("Experience with JavaScript") == {"JavaScript": 1}   # not also Java


def test_vocab_case_sensitive_terms_avoid_false_positives(vocab):
    assert "Excel" not in vocab.find("You will excel in a fast-paced team")
    assert "Excel" in vocab.find("Advanced Excel skills")
    assert "Go" not in vocab.find("Ready, set, go forward")
    assert "Spring Framework" not in vocab.find("Internship starting Spring 2027")


def test_vocab_learned_terms_persist(tmp_path, vocab):
    conn = db.connect(tmp_path / "v.sqlite")
    v = Vocabulary.load()
    v.add("Looker Studio", "tool", ["data studio"], source="learned")
    v.save_term(conn, "Looker Studio")
    reloaded = Vocabulary.load(conn)
    assert "Looker Studio" in reloaded.find("build reports in Data Studio")


# ---- mining -------------------------------------------------------------------
def test_mining_finds_recurring_unknown_terms_only(vocab):
    listings = [
        parse_listing(f"Role {i}\nCo\nRequirements\n- Experience with Palantir Foundry\n- SQL knowledge\n- Ability to work")
        for i in range(3)
    ]
    terms = {c["term"] for c in mine_candidates(listings, vocab)}
    assert "palantir foundry" in terms or "palantir" in terms
    assert "sql" not in terms                    # already in vocabulary
    assert "experience" not in terms and "ability" not in terms
    single = mine_candidates(listings[:1], vocab)
    assert single == []                          # needs >= 2 listings


# ---- resume years ---------------------------------------------------------------
def test_years_estimate_merges_overlaps():
    sections = {"experience": ["Dev 2018 - 2020", "Dev 2019 - 2022", "Lead Mar 2023 - Present"]}
    assert estimate_years_experience(sections, now_year=2026) == 7.0   # 2018-2022 (4) + 2023-2026 (3)
    assert estimate_years_experience({"experience": ["no dates"]}) is None


# ---- scoring (keyword layer, no model) --------------------------------------------------
def test_keyword_scoring_statuses(vocab):
    listing = parse_listing(ANALYST)
    idf = idf_weights([set(vocab.find(ANALYST))])
    result = score_listing(resume_dict(), listing, vocab, idf, embedder=None,
                           target_titles=["Data Analyst"], wanted_levels=["entry"], resume_years=4)
    req = result["required"]
    assert req["SQL"]["status"] == "matched"
    assert req["Tableau"]["status"] == "matched"
    assert req["Power BI"]["status"] == "alternative"      # "Tableau or Power BI" and the resume has Tableau
    assert req["Communication"]["status"] == "missing"
    assert result["nice_to_have"]["Python"]["status"] in ("matched", "weak")
    assert result["coverage"]["total"] == len(req)
    assert 0 <= result["coverage"]["percent"] <= 100
    assert result["components"]["title"] == 1.0
    assert result["components"]["level"] == 1.0


def test_weak_when_skill_only_in_skills_list(vocab):
    text = RESUME_TEXT.replace("Tools: Tableau, Excel, Git", "Tools: Tableau, Excel, Git, Jenkins")
    listing = parse_listing("Engineer\nCo\nRequirements\n- Jenkins experience")
    result = score_listing({"raw_text": text, "sections": split_sections(text),
                            "skills_listed": []}, listing, vocab, {})
    assert result["required"]["Jenkins"]["status"] == "weak"


def test_relevant_listing_outranks_unrelated_and_level_mismatch_hurts(vocab):
    idf = idf_weights([set(vocab.find(ANALYST)), set(vocab.find(DEVOPS))])
    kwargs = dict(vocab=vocab, idf=idf, embedder=None, target_titles=["Data Analyst"],
                  wanted_levels=["entry"], resume_years=4)
    good = score_listing(resume_dict(), parse_listing(ANALYST), **kwargs)
    bad = score_listing(resume_dict(), parse_listing(DEVOPS), **kwargs)
    assert good["total"] > bad["total"] + 20
    assert bad["components"]["level"] < good["components"]["level"]
    assert any("years" in w or "level" in w for w in bad["why"])


# ---- scoring (meaning layer) --------------------------------------------------------------
def test_meaning_layer_credits_paraphrased_requirement(vocab, embedder):
    listing = parse_listing(
        "Business Analyst\nCo\nRequirements\n- Experience presenting insights to stakeholders\n- Python scripting")
    idf = {}
    with_model = score_listing(resume_dict(), listing, vocab, idf, embedder=embedder)
    without = score_listing(resume_dict(), listing, vocab, idf, embedder=None)
    assert with_model["components"]["meaning"] is not None
    assert without["components"]["meaning"] is None
    assert with_model["meaning_pairs"], "expected at least one meaning-matched requirement"
    pair = with_model["meaning_pairs"][0]
    assert "stakeholders" in pair["bullet"].lower()


def test_meaning_ranks_related_above_unrelated(vocab, embedder):
    kwargs = dict(vocab=vocab, idf={}, embedder=embedder)
    good = score_listing(resume_dict(), parse_listing(ANALYST), **kwargs)
    bad = score_listing(resume_dict(), parse_listing(DEVOPS), **kwargs)
    assert good["components"]["meaning"] > bad["components"]["meaning"]


def test_cached_embedder_reuses_vectors(tmp_path, embedder):
    conn = db.connect(tmp_path / "e.sqlite")
    cached = CachedEmbedder(embedder, conn)
    first = cached.embed(["Built Tableau dashboards", "Wrote SQL queries"])
    calls = []
    original = embedder.embed
    embedder.embed = lambda texts, *a, **k: calls.append(texts) or original(texts, *a, **k)
    try:
        second = cached.embed(["Wrote SQL queries", "Built Tableau dashboards", "New text"])
    finally:
        embedder.embed = original
    assert calls == [["New text"]]
    assert float(first[0] @ second[1]) > 0.999


# ---- role profiles -----------------------------------------------------------------------
def test_role_profile_aggregates_and_reports_readiness(vocab):
    listings = [parse_listing(ANALYST), parse_listing(ANALYST.replace("Power BI", "Looker")),
                parse_listing("Data Analyst\nCo\nRequirements\n- SQL and Excel\n- 2+ years of experience")]
    profile = build_profile(listings, vocab, resume_terms={"SQL", "Excel", "Tableau"})
    top = {r["term"]: r["percent"] for r in profile["top_required"]}
    assert top["SQL"] == 100
    assert profile["listing_count"] == 3 and profile["confidence"] == "low"
    assert 0 < profile["readiness_percent"] <= 100
    assert profile["typical_years"] in (1, 2)


# ---- regression tests for issues found while calibrating on real output ------------------
def test_or_requirement_marks_other_option_as_alternative(vocab):
    result = score_listing(resume_dict(), parse_listing(ANALYST), vocab, {}, embedder=None)
    power_bi = result["required"]["Power BI"]
    assert power_bi["status"] == "alternative"
    assert power_bi["via"]["have"] == ["Tableau"]
    assert result["coverage"]["counts"]["alternative"] == 1


def test_meaning_cannot_credit_a_named_skill_via_another_matched_skill(vocab, embedder):
    # Resume has Tableau; "Tableau and Kubernetes" is an AND line, so similarity to the
    # Tableau bullet must not make Kubernetes look covered.
    listing = parse_listing("Engineer\nCo\nRequirements\n- Experience with Tableau and Kubernetes")
    result = score_listing(resume_dict(), listing, vocab, {}, embedder=embedder)
    assert result["required"]["Tableau"]["status"] == "matched"
    assert result["required"]["Kubernetes"]["status"] == "missing"


def test_unrelated_listing_gets_low_meaning_component(vocab, embedder):
    result = score_listing(resume_dict(), parse_listing(DEVOPS), vocab, {}, embedder=embedder)
    assert result["components"]["meaning"] < 0.25


def test_paraphrase_pair_is_credited_at_calibrated_threshold(vocab, embedder):
    text = RESUME_TEXT + "- Built REST services that handled 2M requests per day\n"
    sections = split_sections(text)
    resume = {"raw_text": text, "sections": sections, "skills_listed": extract_skill_items(sections)}
    listing = parse_listing("Backend Dev\nCo\nRequirements\n- Experience developing backend APIs")
    result = score_listing(resume, listing, vocab, {}, embedder=embedder)
    assert result["meaning_pairs"] and "REST" in result["meaning_pairs"][0]["bullet"]


def test_mining_skips_phrases_with_stopwords_or_known_skills(vocab):
    texts = ["Data Analyst\nCo\nRequirements\n- Proficiency in SQL and dbt\n- Experience with Looker Studio"] * 2
    terms = {c["term"] for c in mine_candidates([parse_listing(t) for t in texts], vocab)}
    assert "sql and dbt" not in terms and "dbt" not in terms
    assert "looker studio" in terms
