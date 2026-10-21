"""Learn new skill terms from the user's saved listings ("New terms found" review).

Candidates are short phrases that recur across several listings but are not in the
vocabulary yet. Nothing is added automatically: the user confirms each one.
"""
import re
from collections import Counter

from jobhelper.matching.vocabulary import Vocabulary

STOPWORDS = set("""
a an and are as at be been being but by can do does for from has have having how if in into is it its
may more most must no not of on or our out over per so such than that the their them then there these
they this those to under up us we will with within without you your yours who whom what when where which
while also any all each other some both either etc including include includes e.g. i.e. well new able
""".split())

GENERIC = set("""
experience experiences ability abilities strong excellent good great proven demonstrated familiarity
knowledge understanding working work works skills skill years year plus preferred required requirement
requirements qualification qualifications team teams role roles job jobs company position candidate
candidates responsible responsibilities responsibility duties looking seeking join opportunity
environment business using use used related relevant basic advanced similar equivalent degree field
minimum bonus high level highly ideal ideally understand develop developing build building support
supporting ensure ensuring across various multiple least one two three four five six seven eight nine ten
""".split())

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#./-]*")


def _tokens(line: str) -> list[tuple[str, bool]]:
    """(lowercase token, was_capitalised_mid_sentence) pairs."""
    out = []
    for i, m in enumerate(TOKEN_RE.finditer(line)):
        raw = m.group(0).strip("./-")
        if not raw:
            continue
        cap = i > 0 and (raw[0].isupper() or any(c in raw for c in "+#."))
        out.append((raw.lower(), cap))
    return out


def _candidates_from_line(line: str, vocab: Vocabulary) -> dict[str, bool]:
    """phrase -> whether it looks like a proper/technical name."""
    toks = _tokens(line)
    known = vocab.known_names()
    found: dict[str, bool] = {}
    for n in (1, 2, 3):
        for i in range(len(toks) - n + 1):
            words = [t for t, _ in toks[i:i + n]]
            if any(w in STOPWORDS for w in words):               # no stopwords anywhere inside
                continue
            if words[0] in GENERIC or words[-1] in GENERIC:
                continue
            if n == 1 and len(words[0]) < 3:
                continue
            phrase = " ".join(words)
            if phrase in known:                                    # exactly a known skill/alias
                continue
            proper = all(c for _, c in toks[i:i + n])
            found[phrase] = found.get(phrase, False) or proper
    return found


def mine_candidates(listings: list[dict], vocab: Vocabulary, min_listings: int = 2,
                    limit: int = 40) -> list[dict]:
    """listings: parsed listing dicts. Returns [{term, listings, example, words}] best first."""
    df: Counter = Counter()
    looks_proper: dict[str, bool] = {}
    example: dict[str, str] = {}
    for parsed in listings:
        lines = list(parsed.get("requirements", [])) + list(parsed.get("nice_to_have", []))
        if not lines:
            lines = parsed.get("sections", {}).get("intro", [])
        seen_here: set[str] = set()
        for line in lines:
            for phrase, proper in _candidates_from_line(line, vocab).items():
                looks_proper[phrase] = looks_proper.get(phrase, False) or proper
                example.setdefault(phrase, line)
                if phrase not in seen_here:
                    seen_here.add(phrase)
                    df[phrase] += 1

    # multi-word skills we already know ("looker studio"): their fragments ("studio") and
    # extensions ("looker studio dashboards") are not new skills
    known_multiword = [f" {n} " for n in vocab.known_names() if " " in n]

    results = []
    for phrase, count in df.items():
        if count < min_listings:
            continue
        padded = f" {phrase} "
        if any(padded in k or k in padded for k in known_multiword):
            continue
        words = phrase.split()
        # a single word must look like a name/technology (capitalised or containing + # . digits)
        if len(words) == 1 and not (looks_proper[phrase] or any(c in phrase for c in "+#.0123456789")):
            continue
        results.append({"term": phrase, "listings": count, "example": example[phrase], "words": len(words)})

    # Most frequent first. Among equally frequent overlapping phrases keep the most skill-like
    # one: two words ("looker studio") beat three ("looker studio dashboards") beat one ("studio").
    word_rank = {2: 0, 3: 1, 1: 2}
    results.sort(key=lambda r: (-r["listings"], word_rank[r["words"]], r["term"]))
    kept: list[dict] = []
    for r in results:
        overlaps = any(r["listings"] == k["listings"]
                       and (f" {r['term']} " in f" {k['term']} " or f" {k['term']} " in f" {r['term']} ")
                       for k in kept)
        if not overlaps:
            kept.append(r)
    return kept[:limit]
