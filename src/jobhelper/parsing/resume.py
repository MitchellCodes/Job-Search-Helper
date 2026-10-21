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


def read_docx(path) -> tuple[str, dict]:
    """Return (text, format_flags). A DOCX is a zip of XML, so no extra library is needed."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        root = ET.fromstring(z.read("word/document.xml"))
        paragraphs = []
        for p in root.iter(f"{W}p"):
            paragraphs.append("".join(t.text or "" for t in p.iter(f"{W}t")))
        header_footer_text = ""
        for n in names:
            if re.match(r"word/(header|footer)\d*\.xml", n):
                hroot = ET.fromstring(z.read(n))
                header_footer_text += "".join(t.text or "" for t in hroot.iter(f"{W}t"))
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


def extract_skill_items(sections: dict) -> list[str]:
    items = []
    for line in sections.get("skills", []):
        line = line.split(":", 1)[-1]
        for part in re.split(r"[,;|•·]", line):
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


_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_RANGE_RE = re.compile(
    rf"(?:{_MONTH}\s+)?((?:19|20)\d{{2}})\s*(?:-|–|—|to)\s*(present|current|now|(?:{_MONTH}\s+)?((?:19|20)\d{{2}}))",
    re.I,
)


def estimate_years_experience(sections: dict, now_year: int | None = None) -> float | None:
    """Rough total years of work from date ranges in the experience section (overlaps merged)."""
    import datetime

    now_year = now_year or datetime.date.today().year
    lines = sections.get("experience") or []
    intervals = []
    for line in lines:
        for m in _RANGE_RE.finditer(line):
            start = int(m.group(1))
            end = now_year if m.group(2).lower() in ("present", "current", "now") else int(m.group(3))
            if end >= start:
                intervals.append((start, max(end, start + 0.5)))
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
