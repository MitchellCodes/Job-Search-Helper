"""Replace personal information with placeholders before anything is sent to Claude, and put it back.

What is hidden: your name, email, phone, locations, profile/website links, employer names, school
names, and project and certificate names, plus any extra words the user adds. Detection is
best-effort (patterns and the resume's layout), so the app (1) shows the user exactly what will be
sent, (2) lets them add anything missed, and (3) runs a separate leak check and refuses to send if
it finds something that looks personal.

The header block (name, contact line) is never sent at all; the export rebuilds it from the original.
"""
import re
from dataclasses import dataclass, field

from jobhelper.tailoring.layout import ENTRY_SECTIONS, SEPARATOR, classify, strip_bullet

US_STATES = ("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY "
             "NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC").split()
CA_PROVINCES = "AB BC MB NB NL NS ON PE QC SK".split()
STATE_NAMES = ["Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware",
               "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky",
               "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi", "Missouri",
               "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey", "New Mexico", "New York",
               "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island",
               "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
               "West Virginia", "Wisconsin", "Wyoming"]

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(r"(?<![\w.])(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?![\w])|\+\d[\d\s().-]{7,}\d")
_PROFILE_SITES = (r"linkedin\.com|github\.com|gitlab\.com|bitbucket\.org|twitter\.com|x\.com|facebook\.com|"
                  r"instagram\.com|behance\.net|dribbble\.com|medium\.com|kaggle\.com|leetcode\.com|"
                  r"stackoverflow\.com|youtube\.com|substack\.com|about\.me|portfolio\.[\w.]+")
URL_RE = re.compile(rf"(?:https?://|www\.)[^\s,;|)\]]+|\b(?:{_PROFILE_SITES})(?:/[^\s,;|)\]]*)?|\b[\w-]+(?:\.[\w-]+)+/[^\s,;|)\]]+", re.I)
BARE_DOMAIN_RE = re.compile(r"\b[\w-]{2,}(?:\.[\w-]+)*\.(?:com|net|org|io|dev|me|co|ai|app|xyz|info|us|edu|tech|site|page|work)\b", re.I)
_CITY = r"[A-Z][A-Za-z.'’-]*(?: [A-Z][A-Za-z.'’-]*){0,3}"
_REGION = "|".join(sorted(US_STATES + CA_PROVINCES + STATE_NAMES, key=len, reverse=True))
LOCATION_RE = re.compile(rf"\b{_CITY},\s*(?:{_REGION})\b(?:\s+\d{{5}}(?:-\d{{4}})?)?")
AREA_RE = re.compile(r"\bGreater\s+(?:[A-Z][a-z]+\s+){1,3}Area\b|\b(?:[A-Z][a-z]+\s+){1,2}Metro(?:politan)?\s+Area\b")
STREET_RE = re.compile(r"\b\d{1,6}\s+(?:[A-Z][\w.]*\s+){1,4}(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Dr|Drive|Ln|Lane|Way|Ct|Court|Pl|Place|Pkwy|Parkway)\b\.?(?:,?\s*(?:Apt|Suite|Ste|Unit|#)\s*\w+)?")
ZIP_RE = re.compile(r"\b\d{5}(?:-\d{4})?\b")

TITLE_WORDS = set("""analyst engineer developer manager director intern internship specialist associate assistant
coordinator consultant designer scientist administrator representative lead officer technician architect supervisor
advisor adviser executive clerk cashier barista server tutor instructor teacher researcher accountant auditor planner
writer editor producer recruiter trainee apprentice volunteer fellow head vp president owner founder partner agent
operator contractor freelance programmer statistician strategist""".split())
EMPLOYMENT_WORDS = {"contract", "part-time", "full-time", "remote", "hybrid", "temporary", "seasonal", "freelance",
                    "present", "current", "self-employed"}
SCHOOL_RE = re.compile(r"\b(?:University|College|Institute|Polytechnic|Academy|School|Seminary|Conservatory)\b", re.I)

PLACEHOLDER_RE = re.compile(r"\[([A-Z]+(?:_[A-Z]+)*(?:_\d+)?)\]")
KIND_ORDER = ["EMAIL", "LINK", "PHONE", "LOCATION", "NAME", "EMPLOYER", "SCHOOL", "PROJECT", "CERT", "HIDDEN"]


@dataclass
class Item:
    placeholder: str
    original: str
    kind: str
    count: int = 0


@dataclass
class Redaction:
    sections: dict[str, list[str]]             # redacted lines per section, header section removed
    items: list[Item]
    warnings: list[str] = field(default_factory=list)
    leaks: list[str] = field(default_factory=list)
    name: str | None = None

    def lookup(self) -> dict[str, str]:
        return {i.placeholder: i.original for i in self.items}

    def restore(self, text: str) -> str:
        table = self.lookup()
        return PLACEHOLDER_RE.sub(lambda m: table.get(f"[{m.group(1)}]", m.group(0)), text)

    def unknown_placeholders(self, text: str) -> list[str]:
        table = self.lookup()
        return sorted({f"[{m.group(1)}]" for m in PLACEHOLDER_RE.finditer(text) if f"[{m.group(1)}]" not in table})


# -- detection -------------------------------------------------------------------------------------
def _spans(patterns, text: str, taken: list[tuple[int, int]]) -> list[tuple[str, str]]:
    found = []
    for kind, regex in patterns:
        for m in regex.finditer(text):
            s, e = m.span()
            if any(s < te and e > ts for ts, te in taken):
                continue
            taken.append((s, e))
            found.append((kind, m.group(0).strip(" .,;")))
    return found


def detect_contact(text: str, header_text: str = "") -> list[tuple[str, str]]:
    """(kind, original) for emails, links, phones, locations and streets in the text."""
    taken: list[tuple[int, int]] = []
    found = _spans([("EMAIL", EMAIL_RE), ("LINK", URL_RE), ("PHONE", PHONE_RE), ("LOCATION", STREET_RE),
                    ("LOCATION", LOCATION_RE), ("LOCATION", AREA_RE)], text, taken)
    if header_text:                         # bare domains / zip codes only in the contact block
        no_emails = EMAIL_RE.sub(" ", header_text)          # an email's domain isn't a separate link
        found += _spans([("LINK", BARE_DOMAIN_RE), ("LOCATION", ZIP_RE)], no_emails, [])
    return found


def find_name(header_lines: list[str]) -> str | None:
    for line in header_lines[:3]:
        candidate = line.strip()
        words = candidate.split()
        if 2 <= len(words) <= 4 and all(re.fullmatch(r"[A-Za-z][A-Za-z.'’-]*", w) for w in words):
            return candidate
    return None


def _segments(line: str) -> list[str]:
    parts = re.split(r"\s*,\s*|\s+[|–—-]\s+|\t|\s+at\s+", line)
    return [p.strip(" ()") for p in parts if p and p.strip(" ()")]


def _employer_names(left: str) -> list[str]:
    left = LOCATION_RE.sub(" ", left)
    left = re.sub(r"\([^)]*\)", " ", left)
    out = []
    for seg in _segments(left):
        words = re.findall(r"[A-Za-z][A-Za-z-]*", seg.lower())
        if not words or any(w in TITLE_WORDS for w in words) or seg.lower() in EMPLOYMENT_WORDS:
            continue
        if seg in US_STATES or seg in CA_PROVINCES or seg in STATE_NAMES or len(seg) < 2:
            continue
        out.append(seg)
    return out


def detect_entities(sections: dict[str, list[str]]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for line in classify("experience", sections.get("experience", [])):
        if line.kind == "header":
            found += [("EMPLOYER", n) for n in _employer_names(line.left)]
    for line in classify("education", sections.get("education", [])):
        if line.kind == "header":
            found += [("SCHOOL", seg) for seg in _segments(LOCATION_RE.sub(" ", line.left)) if SCHOOL_RE.search(seg)]
    for line in classify("projects", sections.get("projects", [])):
        if line.kind == "header":
            title = re.split(r"\s+[|–—-]\s+|\t|:\s", line.left)[0].strip()
            if len(title) >= 3:
                found.append(("PROJECT", title))
    for section in ("certifications", "awards"):
        for line in classify(section, sections.get(section, [])):
            if line.kind in ("header", "text"):
                found += [("CERT", seg.strip()) for seg in re.split(r"\s+[|–—-]\s+|\t", line.left) if len(seg.strip()) >= 3]
    return found


# -- redaction -----------------------------------------------------------------------------------------
def _boundary(s: str) -> str:
    esc = re.escape(s).replace(r"\ ", r"\s+")
    return (r"(?<![\w])" if s[0].isalnum() else "") + esc + (r"(?![\w])" if s[-1].isalnum() else "")


def redact(resume: dict, extra_terms: list[str] | tuple[str, ...] = ()) -> Redaction:
    sections = resume["sections"]
    header_lines = sections.get("header", [])
    body = {k: list(v) for k, v in sections.items() if k != "header"}
    warnings: list[str] = []

    name = find_name(header_lines)
    if not name:
        warnings.append("Couldn't find your name at the top of the resume. Add it under “Also hide”.")

    # collect (kind, original) candidates: contact details from everywhere, entities from the layout
    all_text = "\n".join(header_lines + [l for ls in body.values() for l in ls])
    candidates = detect_contact(all_text, "\n".join(header_lines)) + detect_entities(sections)
    candidates += [("HIDDEN", t.strip()) for t in extra_terms if t and t.strip()]
    if name:
        candidates.append(("NAME", name))

    seen: dict[str, str] = {}
    ordered: list[tuple[str, str]] = []
    for kind, original in candidates:
        key = re.sub(r"\s+", " ", original).lower()
        if len(key) < 2 or key in seen:
            continue
        seen[key] = kind
        ordered.append((kind, re.sub(r"\s+", " ", original)))

    # placeholders, numbered per kind in a stable order
    counters: dict[str, int] = {}
    items: list[Item] = []
    for kind, original in sorted(ordered, key=lambda ko: (KIND_ORDER.index(ko[0]), ko[1].lower())):
        counters[kind] = counters.get(kind, 0) + 1
        placeholder = "[NAME]" if kind == "NAME" else f"[{kind}_{counters[kind]}]"
        items.append(Item(placeholder, original, kind))

    ci_items = sorted(items, key=lambda i: -len(i.original))
    table_ci = {re.sub(r"\s+", " ", i.original).lower(): i for i in items}
    pieces = [_boundary(i.original) for i in ci_items]
    token_items: list[Item] = []                  # first/last name on their own, exact case only
    if name:
        for part, ph in zip((name.split()[0], name.split()[-1]), ("[FIRST_NAME]", "[LAST_NAME]")):
            if len(part) >= 3 and part.lower() not in table_ci:
                token_items.append(Item(ph, part, "NAME"))
    cs_pieces = [_boundary(t.original) for t in token_items]
    cs_table = {t.original: t for t in token_items}

    pattern = None
    if pieces:
        pattern = re.compile("(?P<ci>(?i:" + "|".join(pieces) + "))" +
                             (("|(?P<cs>" + "|".join(cs_pieces) + ")") if cs_pieces else ""))
    elif cs_pieces:
        pattern = re.compile("(?P<cs>" + "|".join(cs_pieces) + ")")

    def sub(m):
        if m.groupdict().get("ci"):
            item = table_ci[re.sub(r"\s+", " ", m.group(0)).lower()]
        else:
            item = cs_table[m.group(0)]
        item.count += 1
        return item.placeholder

    redacted = {s: [(pattern.sub(sub, line) if pattern else line) for line in lines] for s, lines in body.items()}
    items = items + token_items        # everything found stays listed, even if only in the (unsent) header

    result = Redaction(sections=redacted, items=items, warnings=warnings, name=name)
    result.leaks = find_leaks(result)
    return result


def find_leaks(redaction: Redaction) -> list[str]:
    """Independent check on the text about to be sent. Anything returned blocks sending."""
    text = "\n".join(l for ls in redaction.sections.values() for l in ls)
    leaks = [f"{kind.lower()} still present: {value}" for kind, value in detect_contact(text)]
    flat = re.sub(r"\s+", " ", text).lower()
    for item in redaction.items:
        original = re.sub(r"\s+", " ", item.original)
        if item.placeholder in ("[FIRST_NAME]", "[LAST_NAME]"):
            if re.search(_boundary(original), text):          # exact case, like the replacement
                leaks.append(f"name part still present: {original}")
        elif original.lower() in flat and len(original) >= 2:
            leaks.append(f"{item.kind.lower()} still present: {original}")
    return sorted(set(leaks))


def render_sections(red: Redaction) -> str:
    """The resume text as it will be sent (redacted, header block omitted)."""
    out = []
    for section, lines in red.sections.items():
        out.append(f"## {section.upper()}")
        if section == "summary":
            out.append(" ".join(strip_bullet(l) for l in lines))
            continue
        for line in classify(section, lines):
            if line.kind == "header":
                out.append(f"### {line.text.replace(chr(9), '  ')}")
            elif line.kind == "bullet":
                out.append(f"- {line.text}")
            else:
                out.append(line.text)
        out.append("")
    return "\n".join(out).strip()
