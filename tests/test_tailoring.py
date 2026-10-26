"""Redaction, the checks on Claude's answer, and the Word export, using the fictional sample resume."""
import re
from pathlib import Path

import pytest

from jobhelper.parsing.listing import parse_listing
from jobhelper.parsing.resume import parse_resume_file
from jobhelper.privacy.redact import find_leaks, redact, render_sections
from jobhelper.tailoring.claude_cli import ClaudeFailed
from jobhelper.tailoring.docx_export import build_docx, build_structure, to_text
from jobhelper.tailoring.layout import classify
from jobhelper.tailoring.tailor import (
    PrivacyBlocked, SCHEMA, build_prompts, canon, coverage_findings, eligible_lines, tailor,
    validate_and_restore,
)

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
LISTING_TEXT = """Data Analyst
Northwind Analytics
Requirements
- 3+ years of experience with SQL and Tableau
- Experience with dashboards for business stakeholders
- Business reporting experience
- Familiarity with Looker
- Strong communication skills
Nice to have
- Python
"""

# everything that identifies the fictional person (what must never reach Claude)
PRIVATE = ["Alex Rivera", "Alex", "Rivera", "alex.rivera@example.com", "example.com", "(555) 010-0123", "010-0123",
           "Seattle", "Tacoma", "Brightline", "Harbor Freight", "Cascade Community Health", "Cascade State",
           "Bike Share Demand Forecast", "Google Data Analytics Professional Certificate"]


@pytest.fixture(scope="module", params=["docx", "pdf"])
def resume(request):
    return parse_resume_file(SAMPLES / f"mock_resume_alex_rivera.{request.param}")


@pytest.fixture(scope="module")
def listing():
    return {"raw_text": LISTING_TEXT, "parsed": parse_listing(LISTING_TEXT)}


SCORE = {"required": {"SQL": {"status": "matched"}, "Tableau": {"status": "matched"},
                      "Looker": {"status": "missing"}, "Communication": {"status": "missing"},
                      "Power BI": {"status": "weak"}}}


# ---- redaction -------------------------------------------------------------------------------------
def test_nothing_personal_survives_redaction(resume):
    red = redact(resume)
    sent = render_sections(red)
    for secret in PRIVATE:
        assert secret.lower() not in sent.lower(), f"leaked: {secret}"
    assert red.leaks == [] and find_leaks(red) == []
    kinds = {i.kind for i in red.items}
    assert {"EMAIL", "PHONE", "LOCATION", "NAME", "EMPLOYER", "SCHOOL", "PROJECT", "CERT"} <= kinds


def test_the_header_block_is_not_sent_at_all(resume):
    red = redact(resume)
    assert "header" not in red.sections and "## HEADER" not in render_sections(red)


def test_restore_is_exact_round_trip(resume):
    red = redact(resume)
    for section, lines in resume["sections"].items():
        if section == "header":
            continue
        for original, hidden in zip(lines, red.sections[section]):
            assert " ".join(red.restore(hidden).split()) == " ".join(original.split())


def test_all_the_kinds_the_user_asked_for_are_hidden(resume):
    sent = render_sections(redact(resume))
    for placeholder in ("[LOCATION_1]", "[EMPLOYER_1]", "[SCHOOL_1]", "[PROJECT_1]", "[CERT_1]"):
        assert placeholder in sent


def test_extra_terms_are_hidden_and_the_leak_check_catches_a_miss(resume):
    red = redact(resume, extra_terms=["Confluence"])
    assert "confluence" not in render_sections(red).lower()
    assert any(i.kind == "HIDDEN" and i.original == "Confluence" for i in red.items)
    # simulate a detector miss: personal text slips through into the redacted lines
    red.sections["summary"].append("Reach me at jane.doe@mail.org or https://github.com/janedoe")
    leaks = find_leaks(red)
    assert any("email" in l for l in leaks) and any("link" in l for l in leaks)


def test_unrecognised_name_is_reported_not_silently_ignored():
    fake = {"sections": {"header": ["Curriculum Vitae of the candidate #1"], "skills": ["Python"]}}
    red = redact(fake)
    assert red.warnings and "name" in red.warnings[0].lower()


@pytest.mark.parametrize("header_extra, expected_kind", [
    ("linkedin.com/in/some-person", "LINK"), ("https://github.com/someone", "LINK"),
    ("www.myportfolio.dev", "LINK"), ("42 Maple Street, Apt 4", "LOCATION"),
    ("Portland, Oregon 97201", "LOCATION"), ("Greater Boston Area", "LOCATION"),
    ("+44 20 7946 0958", "PHONE"), ("555.867.5309", "PHONE"),
])
def test_detects_common_contact_formats(header_extra, expected_kind):
    resume = {"sections": {"header": ["Pat Example", f"pat@example.org | {header_extra}"], "skills": ["Python"]}}
    red = redact(resume)
    assert any(i.kind == expected_kind and header_extra.split(",")[0].split(" Apt")[0] in i.original or
               (i.kind == expected_kind and i.original in header_extra) for i in red.items), \
        [(i.kind, i.original) for i in red.items]


def test_tech_names_with_dots_are_not_mistaken_for_websites():
    resume = {"sections": {"header": ["Pat Example", "pat@example.org"],
                           "skills": ["Node.js, ASP.NET, Vue.js, Socket.io, Next.js, D3.js"]}}
    red = redact(resume)
    assert not any(i.kind == "LINK" for i in red.items)
    assert "Node.js" in render_sections(red)


# ---- what is asked and what comes back ---------------------------------------------------------------
def test_prompt_contains_listing_findings_and_only_redacted_resume(resume, listing):
    system, user = build_prompts(listing, SCORE, redact(resume))
    assert "Northwind Analytics" in user and "Requirements the resume" not in user
    assert "Required skills with no evidence found in the resume: Looker, Communication" in user
    assert "Required skills that appear only in the skills list" in user
    for secret in PRIVATE:
        assert secret.lower() not in (system + user).lower()
    assert "Never invent" in system and "verbatim" in system
    assert set(SCHEMA["required"]) == {"summary", "skills_to_add", "bullet_edits", "gaps_not_to_claim", "notes"}


def test_coverage_findings_text():
    assert "no recognised" in coverage_findings({"required": {}})


def _bullet(red, starts_with):
    """A bullet exactly as the app sees it (wrapped PDF lines already merged)."""
    return next(b for b in eligible_lines(red).values() if starts_with in b)


def _good_reply(red):
    original = _bullet(red, "Built and maintain")
    return {
        "summary": {"suggested": "Data analyst with four years of experience building SQL and Tableau dashboards "
                                 "that give business stakeholders clear reporting.", "reason": "Mirrors the listing"},
        "skills_to_add": [
            {"skill": "Business Reporting", "evidence": "Built and maintain 8 Tableau dashboards", "reason": "listing asks"},
            {"skill": "Looker", "evidence": "I used Looker daily", "reason": "listing asks"},           # fabricated evidence
            {"skill": "Python", "evidence": "Python (pandas, NumPy, matplotlib)", "reason": "nice to have"},  # already there
        ],
        "bullet_edits": [
            {"original": original,
             "suggested": original.replace("Tableau dashboards", "Tableau dashboards for business stakeholders"),
             "keywords_added": ["stakeholders"], "reason": "mirrors listing wording"},
            {"original": "A bullet that is nowhere in the resume", "suggested": "Something else entirely",
             "keywords_added": [], "reason": "hallucinated source"},
            {"original": "Wrote SQL queries to pull carrier performance data and flagged two underperforming vendors "
                         "for contract review",
             "suggested": "Wrote SQL queries to pull carrier performance data and flagged 3 underperforming vendors "
                          "for review, saving $40,000 with Looker",
             "keywords_added": ["Looker"], "reason": "invented number and tool"},
        ],
        "gaps_not_to_claim": [{"skill": "Looker", "advice": "Learn it; don't list it yet."}],
        "notes": "Keep it to one page.",
    }


def test_every_suggestion_is_checked_before_the_user_sees_it(resume, listing):
    red = redact(resume)
    result = validate_and_restore(_good_reply(red), red, listing["raw_text"])
    summary, skills, edits = result["summary"], result["skills_to_add"], result["bullet_edits"]

    assert summary["valid"] and summary["flags"] == []
    by_name = {s["skill"]: s for s in skills}
    assert by_name["Business Reporting"]["valid"] and by_name["Business Reporting"]["flags"] == []
    assert not by_name["Looker"]["valid"] and "evidence" in by_name["Looker"]["flags"][0]["text"]
    assert not by_name["Python"]["valid"] and by_name["Python"]["flags"][0]["level"] == "info"

    good, hallucinated, invented = edits
    assert good["valid"] and "stakeholders" in good["suggested"]
    # the resume says "stakeholder communication", never "stakeholders": adding that listing word must be flagged
    assert [f["level"] for f in good["flags"]] == ["warn"]
    assert "adds stakeholders from the listing" in good["flags"][0]["text"]
    assert not hallucinated["valid"] and "isn't a bullet in your resume" in hallucinated["flags"][0]["text"]
    assert invented["valid"]                                  # source exists; the risky parts are flagged instead
    flag_text = " ".join(f["text"] for f in invented["flags"])
    assert "number(s) not in your original" in flag_text and "40,000" in flag_text
    assert "Looker" in flag_text                              # new tool found in the listing but not the resume
    assert result["gaps"] == [{"skill": "Looker", "advice": "Learn it; don't list it yet."}]


def test_placeholders_are_restored_and_bad_ones_block(resume, listing):
    red = redact(resume)
    original = _bullet(red, "Documented reporting procedures")
    raw = {"summary": {"suggested": "", "reason": ""}, "skills_to_add": [], "gaps_not_to_claim": [], "notes": "",
           "bullet_edits": [{"original": original, "suggested": original + " at [EMPLOYER_99]",
                             "keywords_added": [], "reason": "x"}]}
    out = validate_and_restore(raw, red, listing["raw_text"])
    edit = out["bullet_edits"][0]
    assert not edit["valid"] and "placeholder" in edit["flags"][0]["text"]

    ok = {**raw, "bullet_edits": [{"original": original, "suggested": original + " for [EMPLOYER_1]", "keywords_added": [],
                                   "reason": "x"}]}
    restored = validate_and_restore(ok, red, listing["raw_text"])["bullet_edits"][0]
    assert restored["valid"] and "Brightline Retail Group" in restored["suggested"]
    assert "[" not in restored["suggested"]


def test_tailor_refuses_to_send_when_the_leak_check_fails(resume, listing):
    red = redact(resume)
    red.leaks = ["email still present: someone@example.com"]
    called = []
    with pytest.raises(PrivacyBlocked, match="Not sent"):
        tailor(listing, SCORE, red, runner=lambda *a, **k: called.append(1))
    assert called == []                                       # nothing was handed to Claude


def test_tailor_runs_end_to_end_with_a_scripted_claude(resume, listing):
    red = redact(resume)
    seen = {}

    def scripted(user, schema, system, model=None, timeout=0):
        seen.update(user=user, system=system, model=model)
        return {"data": _good_reply(red), "seconds": 1.5, "cost_usd": 0.02, "model": "scripted"}

    out = tailor(listing, SCORE, red, model="m1", runner=scripted)
    assert seen["model"] == "m1"
    for secret in PRIVATE:
        assert secret.lower() not in (seen["user"] + seen["system"]).lower()
    assert out["meta"]["seconds"] == 1.5 and out["meta"]["model"] == "scripted" and out["meta"]["sent_chars"] > 500


def test_claude_failure_propagates_as_a_readable_error(resume, listing):
    def failing(*a, **k):
        raise ClaudeFailed("Claude took longer than 300 seconds. Try again.")

    with pytest.raises(ClaudeFailed, match="took longer"):
        tailor(listing, SCORE, redact(resume), runner=failing)


# ---- the Word export -----------------------------------------------------------------------------------
def test_export_applies_only_accepted_edits_and_restores_real_details(tmp_path, resume, listing):
    red = redact(resume)
    result = validate_and_restore(_good_reply(red), red, listing["raw_text"])
    good_id = result["bullet_edits"][0]["id"]
    bad_id = result["bullet_edits"][2]["id"]                  # valid but the user does NOT accept it
    structure = build_structure(resume, result, accepted={good_id, "sum", result["skills_to_add"][0]["id"],
                                                          result["bullet_edits"][1]["id"]})
    text = to_text(structure)
    assert "for business stakeholders" in text                                    # accepted edit applied
    assert "saving $40,000" not in text and "3 underperforming" not in text       # unaccepted edit left alone
    assert "Looker" not in text                                                   # fabricated skill not added
    assert "Business Reporting" in text                                           # accepted skill added
    assert "give business stakeholders clear reporting" in text                   # accepted summary
    for real in ("Alex Rivera", "alex.rivera@example.com", "(555) 010-0123", "Brightline Retail Group",
                 "Cascade State University", "Bike Share Demand Forecast"):
        assert real in text                                                       # personal details are back

    path = build_docx(structure, tmp_path / "tailored.docx")
    again = parse_resume_file(path)
    assert again["contact"] == {"email": "alex.rivera@example.com", "phone": "(555) 010-0123"}
    assert {"summary", "skills", "experience", "projects", "education", "certifications"} <= set(again["sections"])
    assert any("for business stakeholders" in l for l in again["sections"]["experience"])
    assert again["format_flags"]["has_tables"] is False and again["format_flags"]["max_columns"] == 1
    from jobhelper.parsing.resume import estimate_years_experience
    assert estimate_years_experience(again["sections"]) == estimate_years_experience(resume["sections"])


def test_export_with_nothing_accepted_reproduces_the_original_content(tmp_path, resume, listing):
    red = redact(resume)
    result = validate_and_restore(_good_reply(red), red, listing["raw_text"])
    path = build_docx(build_structure(resume, result, accepted=set()), tmp_path / "same.docx")
    again = parse_resume_file(path)

    def shape(parsed, section):
        return [(l.kind, l.left, l.date) if l.kind == "header" else (l.kind, canon(l.text))
                for l in classify(section, parsed["sections"][section])]

    for section in ("experience", "projects", "education", "certifications"):
        assert shape(again, section) == shape(resume, section), section
    assert again["skills_listed"] == resume["skills_listed"]
