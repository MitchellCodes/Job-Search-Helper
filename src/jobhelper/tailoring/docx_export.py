"""Build the tailored resume: the user's real text, plus only the edits they accepted, in a plain
single-column, ATS-friendly Word layout (standard headings, real bullets, dates on the right).

Built from the original (un-redacted) resume text, so personal details come from the user's own
file and never depend on what Claude returned.
"""
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from jobhelper.tailoring.layout import classify
from jobhelper.tailoring.tailor import canon

HEADINGS = {"summary": "Professional Summary", "skills": "Skills", "experience": "Professional Experience",
            "projects": "Projects", "education": "Education", "certifications": "Certifications",
            "awards": "Awards"}
W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
TEXT_WIDTH = 10080          # 8.5in page, 0.75in margins (twentieths of a point)


@dataclass
class Block:
    kind: str               # header | bullet | text
    text: str
    bold: str = ""          # for headers: the part to bold
    date: str | None = None


@dataclass
class Structure:
    name: str
    contact: list[str]
    sections: list[tuple[str, list[Block]]] = field(default_factory=list)


def _bold_part(left: str, section: str) -> str:
    if section in ("projects", "certifications", "awards"):
        return left.split(" | ")[0].split("  |  ")[0].strip()
    return left.split(",", 1)[0].strip()


def build_structure(resume: dict, result: dict, accepted: set[str]) -> Structure:
    """Apply the accepted edits (by id) to the resume's own text."""
    sections = resume["sections"]
    header = sections.get("header", [])
    structure = Structure(name=header[0] if header else "", contact=header[1:])

    bullet_edits = {canon(e["original"]): e["suggested"] for e in result.get("bullet_edits", [])
                    if e["id"] in accepted and e.get("valid")}
    summary = result.get("summary")
    new_summary = summary["suggested"] if summary and summary["id"] in accepted and summary.get("valid") else None
    new_skills = [s["skill"] for s in result.get("skills_to_add", []) if s["id"] in accepted and s.get("valid")]

    for key, lines in sections.items():
        if key == "header":
            continue
        blocks: list[Block] = []
        if key == "summary":
            text = new_summary or " ".join(" ".join(l.split()) for l in lines)
            blocks.append(Block("text", text))
        elif key == "skills":
            texts = [l.strip() for l in lines]
            if new_skills:
                if texts:
                    texts[-1] = texts[-1].rstrip(" ,;") + ", " + ", ".join(new_skills)
                else:
                    texts = ["Skills: " + ", ".join(new_skills)]
            blocks = [Block("text", t) for t in texts]
        else:
            for line in classify(key, lines):
                if line.kind == "header":
                    blocks.append(Block("header", line.left, _bold_part(line.left, key), line.date))
                else:
                    text = bullet_edits.get(canon(line.text), line.text)
                    blocks.append(Block(line.kind, text))
        structure.sections.append((HEADINGS.get(key, key.title()), blocks))
    if new_skills and not any(h == HEADINGS["skills"] for h, _ in structure.sections):
        structure.sections.append((HEADINGS["skills"], [Block("text", "Skills: " + ", ".join(new_skills))]))
    return structure


def to_text(structure: Structure) -> str:
    out = [structure.name, *structure.contact, ""]
    for heading, blocks in structure.sections:
        out.append(heading.upper())
        for b in blocks:
            if b.kind == "header":
                out.append(b.text + (f"  ({b.date})" if b.date else ""))
            elif b.kind == "bullet":
                out.append(f"- {b.text}")
            else:
                out.append(b.text)
        out.append("")
    return "\n".join(out).strip() + "\n"


# -- Word file ------------------------------------------------------------------------------------------
def _run(text, bold=False, italic=False, size=None):
    rpr = ("<w:b/>" if bold else "") + ("<w:i/>" if italic else "") + (f'<w:sz w:val="{size}"/>' if size else "")
    rpr = f"<w:rPr>{rpr}</w:rPr>" if rpr else ""
    return f'<w:r>{rpr}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def _para(runs, after=0, before=0, center=False, tabs=False, border=False, bullet=False, keep=False):
    ppr = ("<w:keepNext/>" if keep else "")
    ppr += '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>' if bullet else ""
    ppr += '<w:pBdr><w:bottom w:val="single" w:sz="6" w:space="1" w:color="444444"/></w:pBdr>' if border else ""
    ppr += f'<w:tabs><w:tab w:val="right" w:pos="{TEXT_WIDTH}"/></w:tabs>' if tabs else ""
    ppr += f'<w:spacing w:before="{before}" w:after="{after}"/>' + ('<w:jc w:val="center"/>' if center else "")
    return f"<w:p><w:pPr>{ppr}</w:pPr>{runs}</w:p>"


def build_docx(structure: Structure, path: str | Path) -> Path:
    tab = "<w:r><w:tab/></w:r>"
    body = [_para(_run(structure.name, bold=True, size=40), center=True, after=20)]
    for line in structure.contact:
        body.append(_para(_run(line), center=True, after=20))
    for heading, blocks in structure.sections:
        body.append(_para(_run(heading.upper(), bold=True, size=22), before=180, after=80, border=True, keep=True))
        for b in blocks:
            if b.kind == "header":
                if b.bold and b.text.startswith(b.bold):
                    runs = _run(b.bold, bold=True) + _run(b.text[len(b.bold):])
                else:
                    runs = _run(b.text, bold=True)
                if b.date:
                    runs += tab + _run(b.date, italic=True)
                body.append(_para(runs, before=100, after=20, tabs=bool(b.date), keep=True))
            elif b.kind == "bullet":
                body.append(_para(_run(b.text), after=20, bullet=True))
            else:
                body.append(_para(_run(b.text), after=40))
    sect = ('<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="900" w:right="1080" w:bottom="900" '
            'w:left="1080" w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>')
    document = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {W}><w:body>{"".join(body)}{sect}</w:body></w:document>'
    styles = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles {W}><w:docDefaults><w:rPrDefault><w:rPr>'
              '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/><w:sz w:val="21"/><w:szCs w:val="21"/></w:rPr></w:rPrDefault>'
              '<w:pPrDefault><w:pPr><w:spacing w:after="0" w:line="252" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
              '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style></w:styles>')
    numbering = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:numbering {W}><w:abstractNum w:abstractNumId="0">'
                 '<w:multiLevelType w:val="hybridMultilevel"/><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/>'
                 '<w:lvlText w:val="•"/><w:lvlJc w:val="left"/><w:pPr><w:ind w:left="360" w:hanging="220"/></w:pPr>'
                 '<w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/></w:rPr></w:lvl></w:abstractNum>'
                 '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>')
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                     '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
                     '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
    doc_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/></Relationships>')
    parts = {"[Content_Types].xml": content_types, "_rels/.rels": rels, "word/document.xml": document,
             "word/_rels/document.xml.rels": doc_rels, "word/styles.xml": styles, "word/numbering.xml": numbering}
    for xml in parts.values():
        ET.fromstring(xml)               # well-formed XML or Word refuses to open the file
    path = Path(path)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, xml in parts.items():
            z.writestr(name, xml)
    return path
