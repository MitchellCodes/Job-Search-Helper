"""Fit score: how well a resume matches one listing, with every number explainable.

Components (each 0..1, or None when it can't be judged):
  coverage - share of the listing's required skills the resume covers (by keyword or meaning)
  keyword  - IDF-weighted share covered by keywords alone (listing's distinctive skills count more)
  meaning  - how closely resume bullets match the requirement lines (embedding model)
  title    - listing title vs the titles the user is searching for
  level    - listing level / years required vs the user's chosen level(s) and resume years
"""
import re

import numpy as np

from jobhelper.matching.tfidf import weight

LEVELS = ["entry", "mid", "senior", "lead"]
WEIGHTS = {"coverage": 0.40, "keyword": 0.15, "meaning": 0.15, "title": 0.15, "level": 0.15}

# Cosine similarity at/above which a resume bullet is considered to express a requirement.
# Measured with MiniLM: related requirement/bullet pairs scored 0.24-0.62, unrelated <= 0.21.
MEANING_THRESHOLD = 0.32
WEAK_KEYWORD_CREDIT = 0.7   # skill only appears in the skills list, never in experience
ALTERNATIVE_CREDIT = 0.8    # "X or Y" requirement where the resume has the other option
_OR_RE = re.compile(r"\bor\b|/", re.I)
_AND_RE = re.compile(r"\band\b|,|&", re.I)


def _lines(sections: dict, keys) -> list[str]:
    out = []
    for k in keys:
        out.extend(sections.get(k, []))
    return out


def resume_bullets(resume: dict) -> list[str]:
    """Texts the meaning layer compares against: experience-like lines and skill items."""
    sections = resume["sections"]
    lines = _lines(sections, ["summary", "experience", "projects", "certifications", "education", "awards"])
    lines = [l for l in lines if len(l) >= 12]
    lines += resume.get("skills_listed", [])
    if not lines:
        lines = [l.strip() for l in resume["raw_text"].splitlines() if len(l.strip()) >= 12]
    return lines


def _title_tokens(title: str) -> set[str]:
    stop = {"and", "of", "the", "for", "a", "an", "i", "ii", "iii", "-", "/"}
    return {t for t in re.findall(r"[a-z0-9+#]+", (title or "").lower()) if t not in stop}


def title_score(listing_title: str, targets: list[str], embedder=None) -> float | None:
    if not targets or not listing_title:
        return None
    lt = _title_tokens(listing_title)
    best = 0.0
    for target in targets:
        tt = _title_tokens(target)
        if tt and tt <= lt:
            best = 1.0
        elif tt and lt:
            best = max(best, len(tt & lt) / len(tt | lt))
    if embedder is not None and best < 1.0:
        vecs = embedder.embed([listing_title, *targets])
        sim = float((vecs[1:] @ vecs[0]).max())
        best = max(best, float(np.clip((sim - 0.35) / 0.4, 0.0, 1.0)))
    return round(best, 3)


def level_score(listing_level, years_required, wanted_levels, resume_years) -> tuple[float | None, list[str]]:
    notes, parts = [], []
    if listing_level and wanted_levels:
        idx = LEVELS.index(listing_level)
        dist = min(abs(idx - LEVELS.index(w)) for w in wanted_levels)
        parts.append({0: 1.0, 1: 0.55}.get(dist, 0.15))
        if dist:
            notes.append(f"Listing looks {listing_level}-level; you're targeting {', '.join(wanted_levels)}.")
    if years_required is not None and resume_years is not None:
        ratio = min(1.0, resume_years / years_required) if years_required else 1.0
        parts.append(ratio)
        if ratio < 1.0:
            notes.append(f"Asks for {years_required}+ years; your resume shows about {resume_years:g}.")
    return (round(sum(parts) / len(parts), 3) if parts else None), notes


def _requirement_text(listing: dict) -> tuple[list[str], list[str]]:
    reqs = list(listing.get("requirements") or [])
    nice = list(listing.get("nice_to_have") or [])
    if not reqs:  # unlabeled posting: treat everything except benefits/about as requirements
        sections = listing.get("sections", {})
        reqs = _lines(sections, ["intro", "responsibilities"])
    return reqs, nice


def score_listing(resume: dict, listing: dict, vocab, idf: dict, embedder=None,
                  target_titles=None, wanted_levels=None, resume_years=None) -> dict:
    sections = resume["sections"]
    req_lines, nice_lines = _requirement_text(listing)

    # which requirement lines mention which skills
    term_lines: dict[str, list[str]] = {}
    for line in req_lines:
        for term in vocab.find(line):
            term_lines.setdefault(term, []).append(line)
    nice_terms: dict[str, list[str]] = {}
    for line in nice_lines:
        for term in vocab.find(line):
            if term not in term_lines:
                nice_terms.setdefault(term, []).append(line)

    skills_text = " ".join(sections.get("skills", []))
    body_text = " ".join(_lines(sections, ["summary", "experience", "projects", "certifications",
                                           "education", "awards"])) or resume["raw_text"]
    body_terms, skill_terms = set(vocab.find(body_text)), set(vocab.find(skills_text))

    # meaning layer: best resume bullet for every requirement line
    bullets = resume_bullets(resume)
    best_for_line: dict[str, tuple[float, str]] = {}
    meaning_values = []
    if embedder is not None and bullets and req_lines:
        sims = embedder.embed(req_lines) @ embedder.embed(bullets).T
        for i, line in enumerate(req_lines):
            j = int(sims[i].argmax())
            best_for_line[line] = (float(sims[i][j]), bullets[j])
            meaning_values.append(float(sims[i][j]))

    have = body_terms | skill_terms
    found_in: dict[str, set[str]] = {}

    def terms_in(line: str) -> set[str]:
        if line not in found_in:
            found_in[line] = set(vocab.find(line))
        return found_in[line]

    def evaluate(terms: dict[str, list[str]]) -> dict[str, dict]:
        out = {}
        for term, lines in terms.items():
            if term in body_terms:
                status, via = "matched", None
            elif term in skill_terms:
                status, via = "weak", None
            else:
                others = {l: (terms_in(l) - {term}) & have for l in lines}
                alternative = next((l for l in lines if others[l] and _OR_RE.search(l)
                                    and not _AND_RE.search(l)), None)
                # a line's similarity can't vouch for a named skill when another skill in
                # that line is what actually matched the resume bullet
                usable = [l for l in lines if l in best_for_line and not others[l]]
                hit = max((best_for_line[l] + (l,) for l in usable), default=None)
                if alternative:
                    status, via = "alternative", {"requirement": alternative, "have": sorted(others[alternative])}
                elif hit and hit[0] >= MEANING_THRESHOLD:
                    status, via = "meaning", {"similarity": round(hit[0], 3), "bullet": hit[1], "requirement": hit[2]}
                else:
                    status, via = "missing", None
            out[term] = {"status": status, "via": via, "category": vocab.category(term)}
        return out

    required = evaluate(term_lines)
    nice = evaluate(nice_terms)

    if required:
        total_w = sum(weight(idf, t) for t in required)
        kw = sum(weight(idf, t) * {"matched": 1.0, "weak": WEAK_KEYWORD_CREDIT,
                                                         "alternative": ALTERNATIVE_CREDIT}.get(r["status"], 0.0)
                 for t, r in required.items()) / total_w
        covered = sum(1 for r in required.values() if r["status"] != "missing")
        coverage = covered / len(required)
        units = len(required)
    elif req_lines and best_for_line:  # no recognised skills: judge requirement lines directly
        covered = sum(1 for s, _ in best_for_line.values() if s >= MEANING_THRESHOLD)
        coverage, kw, units = covered / len(best_for_line), None, len(best_for_line)
    else:
        covered, coverage, kw, units = 0, None, None, 0

    meaning = (round(float(np.clip((np.mean(meaning_values) - 0.2) / 0.3, 0, 1)), 3)
               if meaning_values else None)
    title = title_score(listing.get("title"), target_titles or [], embedder)
    level, level_notes = level_score(listing.get("level"), listing.get("years_required"),
                                     wanted_levels or [], resume_years)

    comps = {"coverage": coverage, "keyword": kw, "meaning": meaning, "title": title, "level": level}
    comps = {k: (round(v, 3) if v is not None else None) for k, v in comps.items()}
    present = {k: v for k, v in comps.items() if v is not None}
    total = round(100 * sum(WEIGHTS[k] * v for k, v in present.items()) / sum(WEIGHTS[k] for k in present), 1) if present else None

    status_counts = {s: sum(1 for r in required.values() if r["status"] == s)
                     for s in ("matched", "weak", "meaning", "alternative", "missing")}
    why = []
    if required:
        parts = [f"{status_counts['matched']} in your experience"] if status_counts["matched"] else []
        for key, label in (("meaning", "by meaning"), ("weak", "only in your skills list"),
                           ("alternative", "via an 'or' alternative")):
            if status_counts[key]:
                parts.append(f"{status_counts[key]} {label}")
        why.append(f"You cover {covered} of {units} required skills" + (f" ({', '.join(parts)})." if parts else "."))
        missing = [t for t, r in required.items() if r["status"] == "missing"]
        if missing:
            why.append("Missing: " + ", ".join(missing[:8]) + ("..." if len(missing) > 8 else "") + ".")
    why.extend(level_notes)

    return {
        "total": total,
        "components": comps,
        "coverage": {"covered": covered, "total": units,
                     "percent": round(100 * coverage) if coverage is not None else None,
                     "counts": status_counts},
        "required": required,
        "nice_to_have": nice,
        "nice_coverage": (round(100 * sum(1 for r in nice.values() if r["status"] != "missing") / len(nice))
                          if nice else None),
        "meaning_pairs": [{"requirement": l, "bullet": b, "similarity": round(s, 3)}
                          for l, (s, b) in best_for_line.items() if s >= MEANING_THRESHOLD],
        "why": why,
    }
