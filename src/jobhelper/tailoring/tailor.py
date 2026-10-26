"""Resume tailoring with Claude: what Claude is asked to do, and every check the code makes on its answer.

CLAUDE'S JOB (small and clear): given a redacted resume, a job listing and the app's coverage
findings, propose a few truthful rewrites and skills worth adding, as structured JSON.

THE CODE'S JOB: build the prompt, refuse to send if the privacy check fails, then verify every
suggestion before the user sees it:
  * each quoted original line must really exist in the resume (otherwise it can't be applied)
  * a skill may only be added with a verbatim quote from the resume as evidence
  * numbers or claim-like terms that weren't in the resume or listing are flagged
  * placeholders must survive untouched; unknown ones make the suggestion unusable
  * skills the resume has no evidence for are never added; they are listed as gaps
Then the original personal details are put back and the user accepts or rejects each edit.
"""
import re

from jobhelper.privacy.redact import Redaction, render_sections
from jobhelper.tailoring.claude_cli import run_claude
from jobhelper.tailoring.layout import classify

MAX_EDITS = 10

SYSTEM_PROMPT = """You are an ATS resume-tailoring assistant. You receive a resume (personal details already \
replaced by placeholders such as [EMPLOYER_1]), a job listing, and findings about which listing requirements the \
resume does and does not cover. Suggest a small number of truthful edits that help the resume pass applicant-tracking \
screening and read well to a hiring manager.

Rules:
1. Never invent or exaggerate experience, employers, tools, numbers, dates, titles or credentials. Only reword what is \
already in the resume, using the listing's exact terminology where it truthfully describes the same thing.
2. Add a skill to skills_to_add only if the resume gives evidence for it, and quote that evidence verbatim in `evidence`.
3. For skills the listing wants that the resume has no evidence for, do NOT add them. List them in gaps_not_to_claim \
with brief, honest advice.
4. Keep every placeholder in square brackets exactly as written. Never guess what a placeholder stands for.
5. In bullet_edits, `original` must be copied exactly, character for character, from one resume bullet. Suggest at most \
8 edits, the most valuable first. Keep each suggested bullet a similar length and style to its original.
6. summary.suggested may be an empty string if the summary needs no change.
Reply with the JSON object only."""

_STR = {"type": "string"}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "skills_to_add", "bullet_edits", "gaps_not_to_claim", "notes"],
    "properties": {
        "summary": {"type": "object", "additionalProperties": False, "required": ["suggested", "reason"],
                    "properties": {"suggested": _STR, "reason": _STR}},
        "skills_to_add": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["skill", "evidence", "reason"],
            "properties": {"skill": _STR, "evidence": _STR, "reason": _STR}}},
        "bullet_edits": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["original", "suggested", "keywords_added", "reason"],
            "properties": {"original": _STR, "suggested": _STR, "reason": _STR,
                           "keywords_added": {"type": "array", "items": _STR}}}},
        "gaps_not_to_claim": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["skill", "advice"],
            "properties": {"skill": _STR, "advice": _STR}}},
        "notes": _STR,
    },
}


# -- building the prompt -------------------------------------------------------------------------------
def coverage_findings(score: dict) -> str:
    groups = {"matched": [], "weak": [], "meaning": [], "alternative": [], "missing": []}
    for skill, info in score.get("required", {}).items():
        groups[info["status"]].append(skill)
    lines = []
    if groups["matched"] or groups["alternative"]:
        lines.append("Required skills the resume already shows in its experience: "
                     + ", ".join(groups["matched"] + groups["alternative"]))
    if groups["weak"]:
        lines.append("Required skills that appear only in the skills list, not in any experience bullet: "
                     + ", ".join(groups["weak"]))
    if groups["meaning"]:
        lines.append("Required skills the resume covers in different wording than the listing: "
                     + ", ".join(groups["meaning"]))
    if groups["missing"]:
        lines.append("Required skills with no evidence found in the resume: " + ", ".join(groups["missing"]))
    return "\n".join(lines) or "(no recognised required skills)"


def build_prompts(listing: dict, score: dict, redaction: Redaction) -> tuple[str, str]:
    parsed = listing["parsed"]
    facts = [f for f in (f"level: {parsed['level']}" if parsed.get("level") else None,
                         f"years asked: {parsed['years_required']}+" if parsed.get("years_required") is not None else None)
             if f]
    user = (f"JOB LISTING\n{listing['raw_text'].strip()}\n\n"
            f"FINDINGS FROM THE APP (computed by code from the texts, not guesses)\n{coverage_findings(score)}\n"
            + (("Listing details: " + ", ".join(facts) + "\n") if facts else "")
            + f"\nRESUME (personal details replaced with placeholders)\n{render_sections(redaction)}\n")
    return SYSTEM_PROMPT, user


# -- checking Claude's answer ----------------------------------------------------------------------------
def canon(text: str) -> str:
    """Whitespace- and punctuation-style-insensitive form for comparing quoted text."""
    t = (text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
         .replace("–", "-").replace("—", "-").replace(" ", " "))
    return " ".join(t.split())


NUMBER_RE = re.compile(r"\d[\d,.]*\d%?|\d%?")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-/]*")


def _numbers(text: str) -> set[str]:
    return {n.rstrip(".,") for n in NUMBER_RE.findall(text)}


def _claim_terms(text: str) -> list[str]:
    """Words that look like names, tools or acronyms (not the first word of a sentence)."""
    terms = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n", text):
        words = WORD_RE.findall(sentence)
        for i, w in enumerate(words):
            w = w.strip(".-/")
            if len(w) < 2 or re.fullmatch(r"\[[A-Z_0-9]+\]", w):
                continue
            if (i > 0 and w[0].isupper()) or any(c.isdigit() or c in "+#" for c in w) or (w.isupper() and len(w) > 1):
                terms.append(w)
    return terms


def _flag(level: str, text: str) -> dict:
    return {"level": level, "text": text}


def _check_suggestion(original: str, suggested: str, redaction: Redaction, known_words: set[str],
                      listing_words: set[str]) -> tuple[list[dict], bool]:
    flags, ok = [], True
    unknown = redaction.unknown_placeholders(suggested)
    if unknown:
        flags.append(_flag("block", f"uses placeholder(s) that don't exist: {', '.join(unknown)}"))
        ok = False
    lost = [p for p in re.findall(r"\[[A-Z_0-9]+\]", original) if p not in suggested]
    if lost:
        flags.append(_flag("warn", f"dropped a placeholder from the original ({', '.join(lost)})"))
    new_numbers = sorted(_numbers(redaction.restore(suggested)) - _numbers(redaction.restore(original)))
    if new_numbers:
        flags.append(_flag("warn", f"contains number(s) not in your original text: {', '.join(new_numbers)}. "
                                   "Only keep if accurate"))
    new_terms = sorted({t for t in _claim_terms(suggested)
                        if t.lower() not in known_words and t.lower() not in listing_words})
    if new_terms:
        flags.append(_flag("warn", f"introduces term(s) found in neither your resume nor the listing: "
                                   f"{', '.join(new_terms)}"))
    return flags, ok


def eligible_lines(redaction: Redaction) -> dict[str, str]:
    """canon(line) -> line, for the bullets Claude is allowed to rewrite."""
    out = {}
    for section in ("experience", "projects"):
        for line in classify(section, redaction.sections.get(section, [])):
            if line.kind == "bullet":
                out[canon(line.text)] = line.text
    return out


def validate_and_restore(raw: dict, redaction: Redaction, listing_text: str, meta: dict | None = None) -> dict:
    """Check every suggestion, then put the user's real details back. Nothing here trusts Claude's text."""
    resume_text = "\n".join(l for ls in redaction.sections.values() for l in ls)
    resume_canon = canon(resume_text).lower()
    known_words = {w.strip(".-/").lower() for w in WORD_RE.findall(resume_text)}
    listing_words = {w.strip(".-/").lower() for w in WORD_RE.findall(listing_text)}
    bullets = eligible_lines(redaction)
    restore = redaction.restore
    dropped = 0

    # summary
    summary = None
    original_summary = " ".join(" ".join(l.split()) for l in redaction.sections.get("summary", []))
    suggested_summary = (raw.get("summary") or {}).get("suggested", "").strip()
    if suggested_summary and original_summary and canon(suggested_summary) != canon(original_summary):
        flags, ok = _check_suggestion(original_summary, suggested_summary, redaction, known_words, listing_words)
        summary = {"id": "sum", "original": restore(original_summary), "suggested": restore(suggested_summary),
                   "reason": raw["summary"].get("reason", ""), "flags": flags, "valid": ok}

    # skills to add: only with a verbatim quote from the resume as evidence
    skills = []
    for i, s in enumerate(raw.get("skills_to_add", []), 1):
        skill, evidence = s.get("skill", "").strip(), s.get("evidence", "").strip()
        if not skill:
            dropped += 1
            continue
        flags, ok = [], True
        if not evidence or canon(evidence).lower() not in resume_canon:
            flags.append(_flag("block", "the quoted evidence isn't in your resume, so this skill can't be added"))
            ok = False
        if skill.lower() in resume_canon:
            flags.append(_flag("info", "already appears in your resume"))
            ok = False
        if skill.lower() not in canon(listing_text).lower():
            flags.append(_flag("info", "the listing doesn't mention this skill by name"))
        skills.append({"id": f"s{i}", "skill": skill, "evidence": restore(evidence), "reason": s.get("reason", ""),
                       "flags": flags, "valid": ok})

    # bullet edits
    edits = []
    for i, e in enumerate(raw.get("bullet_edits", [])[:MAX_EDITS], 1):
        original, suggested = e.get("original", "").strip(), e.get("suggested", "").strip()
        if not original or not suggested or canon(original) == canon(suggested):
            dropped += 1
            continue
        flags, ok = _check_suggestion(original, suggested, redaction, known_words, listing_words)
        matched = bullets.get(canon(original))
        if matched is None:
            flags.insert(0, _flag("block", "Claude's quoted original isn't a bullet in your resume, "
                                           "so this edit can't be applied automatically"))
            ok = False
        added = [k for k in e.get("keywords_added", []) if k.strip()]
        not_in_listing = [k for k in added if k.lower() not in canon(listing_text).lower()]
        if not_in_listing:
            flags.append(_flag("info", f"keyword(s) not found in the listing: {', '.join(not_in_listing)}"))
        new_to_resume = [k for k in added if k.lower() not in resume_canon]
        if new_to_resume:
            flags.append(_flag("warn", f"adds {', '.join(new_to_resume)} from the listing. Keep only if it is "
                                       "true for you"))
        edits.append({"id": f"b{i}", "original": restore(matched or original), "suggested": restore(suggested),
                      "keywords_added": added, "reason": e.get("reason", ""), "flags": flags, "valid": ok})

    gaps = [{"skill": g.get("skill", ""), "advice": g.get("advice", "")} for g in raw.get("gaps_not_to_claim", [])
            if g.get("skill")]
    return {"summary": summary, "skills_to_add": skills, "bullet_edits": edits, "gaps": gaps,
            "notes": restore(raw.get("notes", "")), "meta": {**(meta or {}), "dropped": dropped}}


def tailor(listing: dict, score: dict, redaction: Redaction, model: str | None = None, runner=run_claude,
           timeout: int = 300) -> dict:
    """Send the redacted resume + listing to Claude and return the checked, restored suggestions."""
    if redaction.leaks:
        raise PrivacyBlocked(redaction.leaks)
    system, user = build_prompts(listing, score, redaction)
    reply = runner(user, SCHEMA, system, model=model, timeout=timeout)
    meta = {"seconds": reply.get("seconds"), "cost_usd": reply.get("cost_usd"), "model": reply.get("model"),
            "sent_chars": len(user)}
    return validate_and_restore(reply["data"], redaction, listing["raw_text"], meta)


class PrivacyBlocked(Exception):
    """The leak check found something personal in the text about to be sent; nothing was sent."""

    def __init__(self, leaks: list[str]):
        super().__init__("Not sent: the privacy check found personal details that weren't hidden: "
                         + "; ".join(leaks))
        self.leaks = leaks
