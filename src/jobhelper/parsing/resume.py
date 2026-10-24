"""Read a resume (PDF/DOCX/TXT) into text, sections, contact info and format flags."""
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

SECTION_ALIASES = {
    "summary": ["summary", "professional summary", "profile", "objective", "about me", "career summary"],
    "experience": ["experience", "work experience", "professional experience", "employment",
                   "employment history", "work history", "relevant experience"],
    "education": ["education", "academic background", "education and training"],
    "skills": ["skills", "technical skills", "core competencies", "key skills", "skills and tools",
               "technologies", "competencies"],
    "projects": ["projects", "personal projects", "selected projects", "academic projects"],
    "certifications": ["certifications", "certificates", "licenses", "licenses and certifications"],
    "awards": ["awards", "honors", "honors and awards", "achievements"],
}
_HEADING_LOOKUP = {a: key for key, names in SECTION_ALIASES.items() for a in names}

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")


def _text_of(element) -> str:
    """Text of a Word element in document order, keeping tabs and line breaks.

    Resumes put dates after a right-aligned tab ("Seattle, WA<TAB>Jan 2024 - Present"); reading
    only the <w:t> text would glue them together ("WAJan 2024").
    """
    parts = []
    # Only look inside runs: a <w:tab> in the paragraph settings merely defines a tab stop.
    for run in element.iter(f"{W}r"):
        for node in run:
            if node.tag == f"{W}t":
                parts.append(node.text or "")
            elif node.tag == f"{W}tab":
                parts.append("\t")
            elif node.tag in (f"{W}br", f"{W}cr"):
                parts.append("\n")
    return "".join(parts)


def read_docx(path) -> tuple[str, dict]:
    """Return (text, format_flags). A DOCX is a zip of XML, so no extra library is needed."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        root = ET.fromstring(z.read("word/document.xml"))
        paragraphs = [_text_of(p) for p in root.iter(f"{W}p")]
        header_footer_text = ""
        for n in names:
            if re.match(r"word/(header|footer)\d*\.xml", n):
                header_footer_text += _text_of(ET.fromstring(z.read(n)))
        cols = [int(c.get(f"{W}num", "1")) for c in root.iter(f"{W}cols")]
        flags = {
            "format": "docx",
            "has_tables": next(root.iter(f"{W}tbl"), None) is not None,
            "has_text_boxes": next(root.iter(f"{W}txbxContent"), None) is not None,
            "has_images": any(n.startswith("word/media/") for n in names),
            "header_footer_text": header_footer_text.strip(),
            "max_columns": max(cols) if cols else 1,
        }
    return "\n".join(paragraphs), flags


def read_pdf(path) -> tuple[str, dict]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    pages = max(1, len(reader.pages))
    flags = {
        "format": "pdf",
        "pages": len(reader.pages),
        "likely_scanned": len(text.strip()) < 100 * pages * 0.2,
    }
    return text, flags


def read_resume_file(path) -> tuple[str, dict]:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return read_docx(path)
    if suffix == ".pdf":
        return read_pdf(path)
    if suffix in (".txt", ".md"):
        return path.read_text(encoding="utf-8", errors="replace"), {"format": "txt"}
    raise ValueError(f"Unsupported resume type: {suffix} (use PDF or DOCX)")


def _heading_key(line: str):
    cleaned = re.sub(r"[^a-z& ]", "", line.lower()).replace("&", "and").strip()
    return _HEADING_LOOKUP.get(cleaned)


def split_sections(text: str) -> dict:
    """Split resume text into named sections; text before the first heading is 'header'."""
    sections: dict[str, list[str]] = {"header": []}
    current = "header"
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        key = _heading_key(stripped) if len(stripped) <= 40 else None
        if key:
            current = key
            sections.setdefault(current, [])
        else:
            sections[current].append(stripped)
    return {k: v for k, v in sections.items() if v}


def extract_contact(text: str) -> dict:
    email = EMAIL_RE.search(text)
    phone = PHONE_RE.search(text)
    return {"email": email.group(0) if email else None, "phone": phone.group(0) if phone else None}


def _split_outside_parens(line: str) -> list[str]:
    """Split on , ; | and bullets, but keep "SQL (PostgreSQL, MySQL)" together."""
    parts, depth, current = [], 0, []
    for ch in line:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        if depth == 0 and ch in ",;|•·":
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return parts


def extract_skill_items(sections: dict) -> list[str]:
    items = []
    for line in sections.get("skills", []):
        line = line.split(":", 1)[-1]
        for part in _split_outside_parens(line):
            part = part.strip(" -•\t")
            if part:
                items.append(part)
    return items


def parse_resume_file(path) -> dict:
    text, flags = read_resume_file(path)
    sections = split_sections(text)
    return {
        "raw_text": text,
        "sections": sections,
        "contact": extract_contact(text),
        "skills_listed": extract_skill_items(sections),
        "format_flags": flags,
    }


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_RANGE_RE = re.compile(
    rf"(?:(?P<m1>{_MONTH})\s+)?(?P<y1>(?:19|20)\d{{2}})\s*(?:-|–|—|to)\s*"
    rf"(?:(?P<now>present|current|now)|(?:(?P<m2>{_MONTH})\s+)?(?P<y2>(?:19|20)\d{{2}}))",
    re.I,
)


def _month_number(name: str | None) -> int | None:
    return _MONTHS.get(name[:3].lower()) if name else None


def estimate_years_experience(sections: dict, now: tuple[int, int] | None = None) -> float | None:
    """Total years of work from date ranges in the experience section (overlaps merged).

    Uses months when given ("Jun 2022 - Dec 2023" = 1.5 years); a bare year counts from the
    start of that year. `now` is (year, month) for "Present"; defaults to today.
    """
    import datetime

    today = datetime.date.today()
    now_year, now_month = now or (today.year, today.month)
    lines = sections.get("experience") or []
    intervals = []
    for line in lines:
        for m in _RANGE_RE.finditer(line):
            m1 = _month_number(m.group("m1"))
            start = int(m.group("y1")) + ((m1 - 1) / 12 if m1 else 0)
            if m.group("now"):
                end = now_year + now_month / 12
            else:
                m2 = _month_number(m.group("m2"))
                end = int(m.group("y2")) + (m2 / 12 if m2 else 0)    # end of the named month
            if end >= start:
                intervals.append((start, max(end, start + 0.25)))
    if not intervals:
        return None
    intervals.sort()
    total, cur_s, cur_e = 0.0, *intervals[0]
    for s_, e_ in intervals[1:]:
        if s_ <= cur_e:
            cur_e = max(cur_e, e_)
        else:
            total += cur_e - cur_s
            cur_s, cur_e = s_, e_
    total += cur_e - cur_s
    return round(total, 1)
