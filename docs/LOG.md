# Project log: plans and changes

What was planned, what changed, and why. Dates come from the session and the git history.
The original plan is in [PLAN.md](PLAN.md). The app was built with Claude Code under my direction;
decisions marked **(me)** were mine.

## Assignment fit (Artifact 3: "AI Inside It")

- **A model does real work every time it runs.** Two models:
  - **Claude (headless Claude Code, `claude -p`)** proposes resume edits every time the user tailors a resume.
    This is the "app that runs Claude Code headless as one step" shape from the assignment.
  - **MiniLM (local)** reads the resume and each listing on every score; its similarity numbers decide which
    requirements count as covered.
- **Cost check:** no API key. Claude Code is covered by the course access; MiniLM is bundled and free.
- **Model vs. code split:** documented in `README.md` and `CLAUDE.md`. Claude only *proposes* edits as JSON;
  the code redacts before sending, refuses to send if a leak check fails, locks Claude down (no tools, empty
  folder), validates every suggestion, restores personal details, and lets the user accept or reject each edit.

## Oct 20 (evening): planning

**Request:** an app that helps search for jobs and tailor a resume to pass ATS screening, using a model but
without paid API costs; asked to be quizzed on open decisions first. The plan went through five versions.
Decisions **(me)**:

| Topic | Planned at first | Decided |
|---|---|---|
| Stack | Python + a UI framework | Portable app on any OS; plain HTML screens in a pywebview window to keep dependencies minimal |
| Distribution | Installer + uninstaller | **Portable app** (no installer); data in a `data/` folder next to it |
| Getting listings | Scrape big sites, or free job APIs, or paste | **No scraping and no job-site APIs** (ToS); I supply each listing myself |
| Screenshots/OCR | listing import only | **Removed** |
| AI approach | embeddings + keywords vs. + local LLM | Embeddings + keywords. I asked whether embeddings were necessary ("no, but then v1 has no trained model"), so I chose **built in and always on** |
| Keyword source | fixed list | A starter list that **learns from the listings I add** for a role and level |
| Dependencies | scikit-learn, PyTorch, web framework considered | Left out; saved as possible v2 additions |
| Browser pane | embed pages in the app | Embedding is blocked by the sites; a companion window (Option A), later removed (below) |
| Claude connection | copy/paste prompts | A local MCP server (stretch). Replaced by the headless design on Oct 25 |

## Oct 20-21: environment and first version (commit `a482d35`)

Python wasn't installed. 3.15 was tried first but `onnxruntime`/`tokenizers` have no wheels for it, so 3.13.
Risks were prototyped first (model load ~0.9 s; PyInstaller bundle 121 MB; two windows and the clipboard worked).
Then: database, resume and listing parsers, skill vocabulary, term mining, the meaning layer, the fit score,
role profiles, a service layer, the API, five screens, and a dev HTTP mode. 43 tests.

Problems found by checking real output rather than trusting passing tests:

| Problem | Fix |
|---|---|
| "Power BI" credited because the line said "Tableau or Power BI" and the resume had Tableau | A line's similarity can't credit a named skill when another skill in that line matched; "X or Y" lines get an *alternative* status |
| Meaning threshold 0.38 missed a true paraphrase | Re-calibrated to 0.32 on 9 pairs. Related pairs scored 0.07 to 0.62; 3 of 5 related were credited, 0 of 4 unrelated |
| Term mining suggested junk ("sql and dbt", a leftover "studio") | Drop phrases with stopwords or fragments of known multi-word skills |
| Saved listings kept results from an older parser | Parser version; saved listings are re-read at startup |
| `.gitignore` rule `data/` also hid the starter vocabulary | Changed to `/data/` before the first push |
| My patch scripts wrote real newlines/backspaces into code | Stopped patching code through string replacement |

## Oct 24: sample resume, parser and window fixes (commits `b56d194`, `4f5daa8`)

- A realistic mock resume exposed parser bugs: tabs between titles and dates dropped, skills in parentheses
  split wrongly, experience ignoring months (4.7 years reported as 3.5), two over-broad aliases.
- My mistake: I rebuilt the app while it was open. No data was lost (verified by hash) and I wrote
  `scripts/build_portable.ps1` to keep and verify the data folder.
- Testing found the companion browser window unusable for copying (pywebview disables text selection and the
  right-click menu by default). A clipboard bug I found while verifying (tkinter's lazy clipboard) was fixed.

## Oct 25 (afternoon): assignment review

I read `artifact3.pdf` and compared it with the repo. The app already had a model doing real work (MiniLM), but the repo
had no write-up of what the model does versus what the code checks, and no log. I added `README.md`, `CLAUDE.md`, this log
and a copy of the original plan. I then decided the project should also put Claude itself in the loop (next entry).

## Oct 25 (evening): redesign: Claude inside the app

**New direction (me):** paste listings in (no integrated window), keep the in-app comparison, and add Claude
tailoring, removing personal information before sending and restoring it after.

**Choosing the Claude shape.** I asked questions and decided between an MCP server and headless `claude -p`:

| Question | My answer | Why it matters |
|---|---|---|
| How to start a request | A button in the app | Headless: the app runs Claude itself; with MCP you chat with Claude and it calls tools |
| Privacy guarantee | The app controls exactly what is sent | With MCP, Claude chooses which tools to call and anything I type reaches it |
| Output | Both edits to review and a finished resume file | |
| What to redact | name, email, phone, locations, links, plus employers, schools, projects and certificates | |
| Browser window | Remove it | |

**Decision: headless Claude Code, not an MCP server.** It is a listed assignment shape, costs nothing extra,
and lets the app guarantee what is sent. The MCP server stays a possible later feature.

**Checked before designing:** the real CLI. One tiny real call confirmed `--tools ""` plus a custom system prompt,
`--json-schema`, an empty working folder and `--output-format json` work together (reply arrives as parsed JSON in
`structured_output`, ~4 s).

**Built (each with tests):** a layout classifier (`tailoring/layout.py`), redaction + restore + leak check
(`privacy/redact.py`), the headless runner (`tailoring/claude_cli.py`), the prompt, schema and reply checks
(`tailoring/tailor.py`), the Word export (`tailoring/docx_export.py`), service/API methods, and the screens: privacy
review, progress, accept/reject with word-level diffs, preview and save. Removed the companion browser, clipboard
code and address box. 103 tests; one more is opt-in and calls real Claude.

**Problems found while building (and fixed):**

| Problem | Fix |
|---|---|
| PDF text differs from DOCX: bold run split from the rest of a line, dates on their own line, wrapped bullets, bullets as `\x7f` | `merge_fragments`: DOCX and PDF now classify identically for the sample resume |
| Redaction listed an email's domain as a separate "link" | Skip emails when scanning for bare domains |
| A tech name such as "ASP.NET" or "Socket.io" could be mistaken for a website | Bare domains are only matched in the contact block; tests cover Node.js, ASP.NET, Vue.js, Socket.io, Next.js, D3.js |
| My tests used wrapped PDF fragments as "original" bullets and a skill the resume already had | Tests now pick bullets the way the app does; the code's rejections were correct |
| The stand-in Claude waited for stdin on `--version`, so the "Claude available" check timed out | Fixed the stand-in, and the version check never inherits stdin |
| Claude takes ~10 s; holding the API lock would freeze the app | `tailor_run` takes the lock only around its database steps; a test proves other requests answer meanwhile |

**Verified:**
- With a stand-in Claude: the process received tools off, an empty working folder, and none of the personal strings; a
  scripted reply containing a fabricated skill, an invented number and a hallucinated bullet was blocked or flagged in the UI;
  the saved Word file had only the accepted edits and the real details back.
- With **real Claude**, through the app's own service: 9 s, `claude-sonnet-5-5`, ~$0.026 at list price. It reworded the
  summary and two bullets with the listing's terms, did not add Looker, and listed it as a gap. Nothing personal was in
  what was sent and no placeholder was left in the result.
- The packaged exe starts, loads the model, and reports Claude Code found.

## Trade-offs (cost, latency, reliability, security)

- **Cost:** no key; uses the Claude Code access. Local MiniLM makes the app ~124 MB.
- **Latency:** ~10 s per tailoring request.
- **Reliability:** Claude can invent, so no suggestion is trusted: unsupported ones are blocked, risky ones flagged and
  unticked. MiniLM is weak on abstract soft skills and its threshold rests on 9 pairs.
- **Security/privacy:** redaction is best-effort (patterns and layout; US-centric locations; misses such as a school
  without "University" in its name). Mitigations: the user reviews the exact text, can add words to hide, the leak check
  blocks sending, the contact block is never sent, and Claude runs with no tools in an empty folder.

## Not done / known gaps

Resume format review, skills-gap dashboard, application tracker, a "Delete all my data" button (backend exists), the MCP
server, and any testing on macOS or Linux. I haven't used the new screens in the native window by hand, only
through the browser test mode and the packaged-app smoke test. Real-world resumes and listings beyond the one fictional
sample haven't been tried, so redaction recall on them is unknown.
