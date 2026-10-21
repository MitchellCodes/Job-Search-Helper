import zipfile

from jobhelper.parsing.listing import parse_listing
from jobhelper.parsing.resume import (
    extract_contact, extract_skill_items, parse_resume_file, split_sections,
)
from jobhelper.storage import db

RESUME = """Jane Doe
jane@example.com | (206) 555-0142

Summary
Data analyst with SQL and Python experience.

Skills
Languages: Python, SQL, R
Tools: Tableau; Excel

Experience
Analyst, Acme Corp
- Built dashboards that cut reporting time by 40%

Education
B.S. Statistics, University of Washington
"""

LISTING = """Junior Data Analyst
Acme Analytics
Location: Seattle, WA (Hybrid)
$60,000 - $75,000 per year

About Us
We make data tools.

Responsibilities
- Build weekly dashboards
- Clean data

Requirements
- 1+ years of experience with SQL
- Proficiency in Tableau or Power BI
- Strong communication skills

Nice to have
- Python
"""


def make_docx(path, body_xml, extra=None):
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    doc = f'<?xml version="1.0"?><w:document {ns}><w:body>{body_xml}</w:body></w:document>'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", doc)
        for name, data in (extra or {}).items():
            z.writestr(name, data)


def paragraphs(lines):
    return "".join(f"<w:p><w:r><w:t>{l}</w:t></w:r></w:p>" for l in lines)


def test_resume_sections_and_contact():
    sections = split_sections(RESUME)
    assert {"header", "summary", "skills", "experience", "education"} <= set(sections)
    assert extract_contact(RESUME) == {"email": "jane@example.com", "phone": "(206) 555-0142"}
    assert extract_skill_items(sections) == ["Python", "SQL", "R", "Tableau", "Excel"]


def test_docx_text_and_format_flags(tmp_path):
    path = tmp_path / "r.docx"
    table = "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>x</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
    make_docx(path, paragraphs(RESUME.splitlines()) + table,
              {"word/header1.xml": '<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>Jane Doe</w:t></w:r></w:p></w:hdr>'})
    parsed = parse_resume_file(path)
    assert "Experience" in parsed["sections"] or "experience" in parsed["sections"]
    assert parsed["format_flags"]["has_tables"] is True
    assert parsed["format_flags"]["header_footer_text"] == "Jane Doe"
    assert parsed["contact"]["email"] == "jane@example.com"


def test_listing_parse():
    p = parse_listing(LISTING)
    assert p["title"] == "Junior Data Analyst"
    assert p["company"] == "Acme Analytics"
    assert p["location"].startswith("Seattle")
    assert p["remote"] == "hybrid"
    assert p["level"] == "entry"
    assert p["years_required"] == 1
    assert p["pay"]["low"].replace(" ", "") == "$60,000"
    assert "Proficiency in Tableau or Power BI" in p["requirements"]
    assert p["nice_to_have"] == ["Python"]
    assert "Build weekly dashboards" in p["sections"]["responsibilities"]


def test_listing_level_from_years_when_title_neutral():
    p = parse_listing("Data Analyst\nCo\nRequirements\n- 7+ years of experience in analytics")
    assert p["level"] == "senior"


def test_storage_dedup_and_tags(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    parsed = parse_listing(LISTING)
    first, created = db.add_listing(conn, LISTING, parsed, search_title="Data Analyst", search_level="entry")
    again, created2 = db.add_listing(conn, "  " + LISTING.upper() + " ", parsed)
    assert created and not created2 and first == again
    row = conn.execute("SELECT search_title, search_level FROM listings").fetchone()
    assert (row["search_title"], row["search_level"]) == ("Data Analyst", "entry")
    rid = db.add_resume(conn, "Main", "r.docx", RESUME, split_sections(RESUME))
    assert rid == 1


def test_level_from_title_numeral():
    assert parse_listing("Data Analyst I\nCo\nRequirements\n- SQL")["level"] == "entry"
    assert parse_listing("Data Analyst II\nCo\nRequirements\n- SQL")["level"] == "mid"
    assert parse_listing("Software Engineer III\nCo\nRequirements\n- Java")["level"] == "senior"
    assert parse_listing("Data Analyst\nCo\nRequirements\n- SQL")["level"] is None
