"""Role keyword profiles: what listings for one title+level typically ask for."""
from collections import Counter


def confidence(n: int) -> str:
    return "low" if n < 5 else "medium" if n < 10 else "good"


def build_profile(listings: list[dict], vocab, resume_terms: set[str] | None = None) -> dict:
    """listings: parsed listing dicts that were imported under one title+level."""
    n = len(listings)
    required: Counter = Counter()
    nice: Counter = Counter()
    years = []
    for parsed in listings:
        reqs = parsed.get("requirements") or parsed.get("sections", {}).get("intro", [])
        req_terms = set()
        for line in reqs:
            req_terms |= set(vocab.find(line))
        nice_terms = set()
        for line in parsed.get("nice_to_have", []):
            nice_terms |= set(vocab.find(line))
        required.update(req_terms)
        nice.update(nice_terms - req_terms)
        if parsed.get("years_required") is not None:
            years.append(parsed["years_required"])

    def rows(counter):
        return [{"term": t, "listings": c, "percent": round(100 * c / n), "category": vocab.category(t)}
                for t, c in counter.most_common()] if n else []

    top_required = rows(required)
    profile = {
        "listing_count": n,
        "confidence": confidence(n),
        "top_required": top_required,
        "nice_to_have": rows(nice),
        "certifications": [r for r in top_required if r["category"] == "certification"],
        "typical_years": sorted(years)[len(years) // 2] if years else None,
    }
    if resume_terms is not None and n:
        weights = {r["term"]: r["listings"] for r in top_required}
        total = sum(weights.values())
        have = sum(c for t, c in weights.items() if t in resume_terms)
        profile["readiness_percent"] = round(100 * have / total) if total else None
    return profile
