"""Tell the kinds of resume lines apart: job/school/project headers, bullets and plain text.

Used by redaction (to find employers, schools, projects) and by the DOCX export (to lay lines
out again). Works on text from either DOCX (bullets arrive without a bullet character) or PDF
(bullets start with one and a header's dates may sit on a line of their own).
"""
import re
from dataclasses import dataclass

_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_YEAR = r"(?:19|20)\d{2}"
_SINGLE = rf"(?:{_MONTH}\s+)?{_YEAR}"
_RANGE = rf"{_SINGLE}\s*(?:-|–|—|to)\s*(?:present|current|now|{_SINGLE})"
TRAILING_RANGE = re.compile(rf"(?:^|[\s|,(]+)({_RANGE})\s*\)?\s*$", re.I)
TRAILING_SINGLE = re.compile(rf"(?:^|[\s|,(]+)({_SINGLE})\s*\)?\s*$", re.I)
# \x7f,  and friends are how PDF text extraction often renders a bullet symbol
BULLET = re.compile(r"^\s*(?:[-*•·▪●◦‣⁃\x7f]|\d+[.)])\s+")
ENTRY_SECTIONS = ("experience", "projects", "education", "certifications", "awards")
SEPARATOR = re.compile(r"\s+[|–—-]\s+|\t")


@dataclass
class Line:
    kind: str            # header | bullet | text
    text: str            # the full line without any bullet marker (headers: "left<TAB>date")
    left: str            # for headers: the text before the dates
    date: str | None     # for headers: the date or date range


def strip_bullet(line: str) -> str:
    return BULLET.sub("", line, count=1).strip()


def split_trailing_date(line: str, ranges_only: bool) -> tuple[str, str | None]:
    """("Analyst, Acme\tJan 2024 - Present") -> ("Analyst, Acme", "Jan 2024 - Present")."""
    m = TRAILING_RANGE.search(line) or (None if ranges_only else TRAILING_SINGLE.search(line))
    if not m:
        return line.strip(), None
    return line[: m.start()].strip(" \t|,"), m.group(1).strip()


def _looks_like_title(line: str) -> bool:
    """A project/school/certificate title: has a separator or is a short phrase with no sentence punctuation."""
    if BULLET.match(line):
        return False
    if SEPARATOR.search(line) and len(line) <= 140:
        return True
    words = line.split()
    return 1 <= len(words) <= 8 and not line.rstrip().endswith((".", ";", ":")) and not re.search(r"\d+%", line)


def merge_fragments(lines: list[str]) -> list[str]:
    """Rejoin lines that PDF extraction split apart.

    - a line starting with "," or "|" continues the line above ("Data Analyst" / ", Acme Corp")
    - a line starting in lowercase/digit/"(" continues a *bulleted* line above (a wrapped bullet)
    DOCX text has no bullet characters, so it is left exactly as it is.
    """
    out: list[str] = []
    chain_marked = False            # does the logical line at the end of `out` start with a bullet?
    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            continue
        marked = bool(BULLET.match(raw))
        if out and not marked and stripped[0] in ",|":
            out[-1] = out[-1].rstrip() + (stripped if stripped[0] == "," else " " + stripped)
        elif out and not marked and chain_marked and (stripped[0].islower() or stripped[0].isdigit() or stripped[0] in "(["):
            out[-1] = out[-1].rstrip() + " " + stripped
        else:
            out.append(raw)
            chain_marked = marked
    return out


def classify(section: str, lines: list[str]) -> list[Line]:
    out: list[Line] = []
    for raw in merge_fragments(lines):
        had_bullet = bool(BULLET.match(raw))
        text = strip_bullet(raw)
        if not text:
            continue
        if section not in ENTRY_SECTIONS:
            out.append(Line("text", text, text, None))
            continue
        left, date = split_trailing_date(text, ranges_only=section in ("experience", "projects"))
        if not had_bullet and date and not left and out and out[-1].date is None:
            # PDF: the dates arrived on their own line; they belong to the line above
            prev = out[-1]
            prev.kind, prev.left, prev.date = "header", prev.text, date
            prev.text = f"{prev.text}\t{date}"
        elif not had_bullet and date and left:
            out.append(Line("header", text, left, date))
        elif not had_bullet and section == "projects" and _looks_like_title(text):
            out.append(Line("header", text, text, None))
        elif (not had_bullet and section in ("education", "certifications", "awards") and _looks_like_title(text)
              and not re.search(r"relevant coursework|gpa", text, re.I)):
            out.append(Line("header", text, text, None))
        elif section in ("experience", "projects"):
            out.append(Line("bullet", text, text, None))
        else:
            out.append(Line("text", text, text, None))
    return out
