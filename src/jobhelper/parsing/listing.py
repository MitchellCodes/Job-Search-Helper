"""Turn pasted/clipboard/file text of a job posting into a structured record.

The text always comes from the user. This module never fetches anything.
"""
import re

# Bump when parsing rules change; saved listings are then re-read from their original text.
PARSER_VERSION = 2

SECTION_HEADINGS = {
    "requirements": [
        "requirements", "qualifications", "minimum qualifications", "basic qualifications",
        "required qualifications", "what you bring", "what you'll need", "what you will need",
        "who you are", "must have", "must haves", "skills", "required skills", "you have",
        "what we're looking for", "about you", "your skills",
    ],
    "nice_to_have": [
        "nice to have", "preferred qualifications", "preferred", "bonus", "bonus points",
        "preferred skills", "plus", "pluses", "desired qualifications", "nice to haves",
        "good to have", "additional qualifications",
    ],
    "responsibilities": [
        "responsibilities", "what you'll do", "what you will do", "duties", "the role",
        "key responsibilities", "your role", "in this role", "day to day", "role overview",
    ],
    "benefits": ["benefits", "perks", "what we offer", "compensation", "total rewards"],
    "about": ["about us", "about the company", "who we are", "company overview", "about the team"],
}
_HEADING_LOOKUP = {h: k for k, hs in SECTION_HEADINGS.items() for h in hs}

LEVEL_PATTERNS = [
    ("entry", r"\b(entry[- ]level|junior|jr\.?|intern(ship)?|new grad(uate)?|associate|early career|graduate)\b"),
    ("lead", r"\b(lead|principal|staff|manager|director|head of|architect)\b"),
    ("senior", r"\b(senior|sr\.?)\b"),
    ("mid", r"\b(mid[- ]level|intermediate)\b"),
]

MONEY = r"\$\s?\d{1,3}(?:,\d{3})+(?:\.\d+)?|\$\s?\d+(?:\.\d+)?\s?[kK]?"
PAY_RE = re.compile(rf"(?P<lo>{MONEY})\s*(?:-|to|–|—)\s*(?P<hi>{MONEY})(?P<per>\s*(?:/|per\s)\s*(?:hour|hr|year|yr|annum|month))?", re.I)
YEARS_RE = re.compile(r"(\d{1,2})\s*(?:\+|-\s*\d{1,2})?\s*(?:or more\s*)?years?(?:'|’)?\s*(?:of\s*)?(?:relevant\s*|professional\s*|related\s*|work\s*|hands-on\s*)*(?:experience|exp)", re.I)
LOCATION_RE = re.compile(r"^(?:location|where)\s*[:\-]\s*(.+)$", re.I | re.M)
COMPANY_RE = re.compile(r"^(?:company|employer)\s*[:\-]\s*(.+)$", re.I | re.M)
BULLET_RE = re.compile(r"^\s*(?:[-*•·▪●]|\d+[.)])\s+")


def _heading_key(line: str):
    cleaned = re.sub(r"[^a-z' ]", "", line.lower()).strip()
    cleaned = cleaned.replace("’", "'")
    return _HEADING_LOOKUP.get(cleaned)


def split_sections(text: str) -> dict:
    sections: dict[str, list[str]] = {"intro": []}
    current = "intro"
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        key = _heading_key(line) if len(line) <= 50 else None
        if key is None and line.endswith(":") and len(line) <= 50:
            key = _heading_key(line.rstrip(":"))
        if key:
            current = key
            sections.setdefault(current, [])
        else:
            sections[current].append(BULLET_RE.sub("", line))
    return {k: v for k, v in sections.items() if v}


# "Data Analyst I / II / III" style numbering
TITLE_NUMERAL = [("entry", re.compile(r"\s(?:I|1)$")), ("mid", re.compile(r"\s(?:II|2)$")),
                 ("senior", re.compile(r"\s(?:III|3)$"))]


def detect_level(title: str, text: str, years) -> str | None:
    for level, pattern in TITLE_NUMERAL:
        if pattern.search((title or "").strip()):
            return level
    for source in (title or "", text[:600]):
        for level, pattern in LEVEL_PATTERNS:
            if re.search(pattern, source, re.I):
                return level
    if years is not None:
        if years <= 2:
            return "entry"
        if years <= 5:
            return "mid"
        return "senior"
    return None


def extract_years(text: str):
    values = [int(m.group(1)) for m in YEARS_RE.finditer(text)]
    return min(values) if values else None


def extract_pay(text: str):
    m = PAY_RE.search(text)
    if not m:
        return None
    return {"text": m.group(0).strip(), "low": m.group("lo"), "high": m.group("hi")}


def extract_remote(text: str):
    t = text.lower()
    if re.search(r"\b(hybrid)\b", t):
        return "hybrid"
    if re.search(r"\b(remote|work from home|wfh|telecommute)\b", t):
        return "remote"
    if re.search(r"\b(on-?site|in[- ]office|in person)\b", t):
        return "onsite"
    return None


def requirement_lines(sections: dict) -> list[str]:
    """Lines the matcher treats as individual requirements (required first)."""
    return list(sections.get("requirements", []))


def parse_listing(text: str, url: str | None = None) -> dict:
    text = text.replace("\r\n", "\n").strip()
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    sections = split_sections(text)

    title = lines[0] if lines and len(lines[0]) <= 120 else None
    company = None
    m = COMPANY_RE.search(text)
    if m:
        company = m.group(1).strip()
    elif len(lines) > 1 and len(lines[1]) <= 80 and not BULLET_RE.match(lines[1]):
        company = lines[1]
    location = None
    m = LOCATION_RE.search(text)
    if m:
        location = m.group(1).strip()

    years = extract_years("\n".join(sections.get("requirements", [])) or text)
    return {
        "title": title,
        "company": company,
        "location": location,
        "url": url,
        "remote": extract_remote(text),
        "pay": extract_pay(text),
        "years_required": years,
        "level": detect_level(title or "", text, years),
        "sections": sections,
        "requirements": requirement_lines(sections),
        "nice_to_have": list(sections.get("nice_to_have", [])),
    }
