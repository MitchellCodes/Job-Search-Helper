# Job Search Helper

A local, portable desktop app that helps tailor a resume to job listings. You upload your resume,
paste in listings you find, and the app (1) scores how well your resume fits each one and shows which
required skills you cover, then (2) asks **Claude** for truthful, ATS-friendly edits to your resume for
a specific listing, with your personal information removed before anything is sent and put back after.

> Course project (TCSS 490, Artifact 3: "AI Inside It"). See [docs/LOG.md](docs/LOG.md) for the
> plan and the running log of changes, and [docs/PLAN.md](docs/PLAN.md) for the original plan.

**No paid API and no API key.** Everything runs on your computer except the tailoring request, which
goes through the Claude Code you already have installed and signed in to.

## Where the AI is, and what the code does

There are two models, each with a narrow job. The assignment asks for a clear split so that when
something goes wrong you can tell whether it was the model or the code.

### 1. Claude (via headless Claude Code): proposes resume edits

When you click **Send to Claude** on a listing, the app runs `claude -p` once. **Claude's only job:**
given a redacted resume, the listing, and the app's coverage findings, return a small JSON object of
suggested edits: a rewritten summary, skills worth adding (each with a quote from the resume as
evidence), bullet rewrites, and skills the resume doesn't support (listed as gaps, never added).

**Everything else is plain code, and every check is on the code's side:**

| Step | What the code does | Where |
|---|---|---|
| Before sending | Replaces your name, email, phone, locations, profile links, employer, school, project and certificate names with placeholders (`[EMPLOYER_1]`...). The contact block is never sent. | `privacy/redact.py` |
| Before sending | **Leak check:** scans the exact text to be sent for anything that still looks personal and **refuses to send** if it finds any. | `redact.find_leaks` |
| Before sending | You see the exact text and every replacement first, and can add more words to hide. Nothing is sent until you press the button. | `web/app.js` |
| Starting Claude | **All tools off** (`--tools ""`), empty temporary working folder, no saved session, no skills, no MCP servers. Data goes through stdin, not the command line. Claude can't read anything else on your computer. | `tailoring/claude_cli.py` |
| Asking | Fixed system prompt (never invent facts, quote evidence, keep placeholders) and a JSON schema the reply must match. | `tailoring/tailor.py` |
| Checking the reply | Each quoted original must be a real bullet in your resume (else it can't be applied). A skill may only be added with a verbatim quote of evidence from your resume. New numbers and claim-like terms that are in neither your resume nor the listing are flagged. Placeholders must survive; unknown ones block the suggestion. | `tailor.validate_and_restore` |
| After | Puts your real details back, and unticks anything flagged "Check" until you confirm it's true. | `tailor.py`, `web/app.js` |
| Output | You accept or reject each edit; the Word file is built from **your own text** plus only the edits you accepted. | `tailoring/docx_export.py` |

The tailoring result and exactly what was sent (the redacted text) are saved locally so you can audit them.

### 2. MiniLM (local, no network): meaning match in the fit score

A small embedding model, **all-MiniLM-L6-v2** (quantized ONNX, ~23 MB, bundled in `src/jobhelper/models/`),
runs on your CPU every time a listing is scored. Its one job: turn text into 384 numbers representing its
meaning, so "Built REST services" can match "backend API experience". Code decides what to do with the
numbers (similarity, a 0.32 threshold, a guard so one skill's match can't vouch for a different named skill,
"X or Y" alternatives, the score weights). If the model can't load, scoring continues on keywords.

## What works today

- Upload a resume (PDF or DOCX); review and correct the parsed text.
- Set job titles and experience level(s).
- Paste in listings you found yourself (one, or several separated by a line containing `====`).
- Ranked "best fits" with filters; per-listing coverage ring, colour-coded skill chips, plain-English
  explanation, and the resume bullets matched by meaning.
- **Tailor with Claude:** review what will be sent, send, then accept or reject each suggestion with word-level
  diffs and warning flags; preview the result; save a clean single-column Word resume.
- Role profiles (what a title+level typically asks for, built from the listings you add) and a "new terms"
  review that teaches the app new skills from your listings.

## Not built yet

Resume format/readability review, skills-gap dashboard, application tracker, a "Delete all my data" button
(the backend exists), and macOS/Linux builds. An MCP server was considered and **not** built: headless Claude
was chosen because the app can run it from a button and control exactly what is sent (see the log).

## Running it

**Portable app (Windows):** run `dist\JobSearchHelper\JobSearchHelper.exe`. No install. Your data lives in a
`data` folder next to it; deleting the folder removes everything. Build it with
`powershell -ExecutionPolicy Bypass -File scripts\build_portable.ps1 -Smoke` (close the app first; the script
keeps your data folder).

**Tailoring needs Claude Code:** install it (claude.com/claude-code), run `claude` once in a terminal and sign in.
Everything except tailoring works without it.

**From source (Python 3.12 or 3.13):**

```
python -m venv .venv
.venv\Scripts\pip install -e .[dev]
.venv\Scripts\python -m jobhelper
```

**Tests:** `python -m pytest` (103 pass). Tests of the Claude step use a stand-in program (`tests/fake_claude.py`)
that records exactly what it was sent, which is how the privacy guarantees are verified. One opt-in test makes a
real Claude request: `JOBHELPER_TEST_CLAUDE=1 python -m pytest tests/test_claude_live.py`.

`python -m jobhelper --dev` serves the same screens at http://127.0.0.1:8765 for testing in a normal browser
(native file dialogs aren't available there).

**Sample data:** `samples/mock_resume_alex_rivera.docx` and `.pdf` are the same fictional resume (no real personal data).

## Cost, speed and reliability trade-offs

- **Cost:** no API key. Tailoring uses your Claude Code access. In my test run one tailoring request took about
  9 seconds and was priced at about $0.03 at list rates (on a subscription it counts toward your plan's limits).
  MiniLM runs locally for free; the cost is size: the Windows app is about 124 MB.
- **Latency:** Claude ~10 s per tailoring request (can be longer). MiniLM loads in ~0.9 s; comparisons take milliseconds.
- **Reliability:** Claude can propose invented details, so the code never trusts it: unsupported edits and skills
  are blocked, risky ones are flagged and unticked. MiniLM is weak on abstract soft skills ("communication" scored
  0.07 against "presented to stakeholders") and its threshold was calibrated on only 9 hand-made pairs.
- **Privacy/security:** personal details are replaced before sending, but **redaction is best-effort.** It uses
  patterns and the resume's layout, is US-centric for locations, and can miss things (for example a school without
  "University" or "College" in its name). That's why you review the exact text first, can add words to hide, and the
  leak check blocks sending if something obvious remains. The app never scrapes or fetches job sites; you paste listings.

## How it is built

Python 3.13, pywebview (native window), plain HTML/CSS/JS screens, SQLite, `onnxruntime` + `tokenizers` + `numpy`
for MiniLM, `pypdf` for PDFs (DOCX is read and written with the standard library). Five runtime dependencies.

```
src/jobhelper/
  app.py            starts the window          service.py   the app's logic
  api.py            what the screens call      devserver.py browser-testing mode
  parsing/          resume + listing readers   storage/      SQLite
  matching/         vocabulary, term mining, TF-IDF, MiniLM wrapper, fit score, role profiles
  privacy/          redaction, restore, leak check
  tailoring/        Claude runner, prompt + reply checks, layout classifier, Word export
  web/              the screens                models/       the bundled MiniLM files
```
