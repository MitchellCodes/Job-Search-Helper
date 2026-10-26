# Original plan (snapshot)

This is the plan as approved on Oct 21 (version 5, after several rounds of changes), kept unchanged as a
record of what was planned. **Parts of it have since changed**; see [LOG.md](LOG.md) for what changed and why.
The main changes: the OCR, job-API and what-if ideas were dropped; on Oct 25 the integrated browser window and
clipboard import were removed (listings are pasted in), and the planned Claude MCP connector was replaced by a
headless Claude Code step that tailors the resume, with personal details redacted before sending. The format
review, skills-gap dashboard and tracker described below are not built yet.

---

# Job Search Helper — Plan (v5)

## Context
Greenfield app (repo has only a README). A free, local, privacy-first, **portable** app that (1) ingests a resume (PDF/DOCX), (2) takes target job titles and experience level, (3) lets the user bring in job listings they find themselves (integrated browser, paste, or file), (4) scores resume-vs-listing fit, and (5) summarizes each posting and suggests ATS- and human-friendly resume tailoring. No paid APIs. Runs on Windows, macOS and Linux, on a modest laptop, with as few dependencies as possible.

**Listing rule: the app never scrapes, crawls, or auto-fetches any job site, and has no job-site APIs or feeds.** The user finds each listing and supplies it. The app only reads what the user pastes, loads from a file, or copies to the clipboard.

## Decisions made
- **Minimal dependencies, with one deliberate exception:** a small neural embedding model (MiniLM, quantized ONNX) **built in and always on in v1**, so the app really "implements a model" at no cost. No scikit-learn, no PyTorch, no web framework.
- **Keywords come from listings the user imports while searching for a specific role and level** (see "Vocabulary and role profiles built from your listings"). The app never searches job sites itself.
- **Browser pane: Option A (side-docked companion window) for now.** Reminder to experiment with Option B (single window, embedded Qt browser) later; see "Reminders."
- **Portable-app distribution** (no installer/uninstaller), with a separate build for each OS.
- Resume input: PDF and DOCX only. No OCR/screenshots. No automated discovery sources. No what-if simulator.
- Experience level is a user setting. Email-alert scanning deferred.
- Optional Claude connection via a **local MCP server** (no API key), plus a copy/paste fallback. Both are off by default and not needed for the core app.

## Stack (kept deliberately small)
| Need | Choice | Extra dependency? |
|---|---|---|
| Language | Python 3.11+ (the programming language the app is written in) | — |
| Window + integrated browser | `pywebview` (uses each OS's built-in web view: WebView2 on Windows, WebKit on macOS/Linux) | **1 (pywebview)** |
| UI | Plain HTML/CSS/JS, talking to Python through pywebview's built-in bridge (no web server, no framework) | none |
| Charts (coverage ring, bars) | Hand-drawn inline SVG | none |
| Database | SQLite (`sqlite3`, built into Python) | none |
| DOCX read/write | Python's `zipfile` + `xml` (a DOCX is a zip of XML); small hand-written writer for ATS-safe templates | none |
| PDF read | `pypdf` (pure Python) | **1 (pypdf)** |
| Keyword matching | Pure-Python TF-IDF and phrase matching using `re`, `collections`, `math` | none |
| Meaning matching (the AI model) | `all-MiniLM-L6-v2`, quantized ONNX (~23 MB, Apache-2.0), run with `onnxruntime`; text prepared with `tokenizers`; vector math with `numpy`. Model file ships inside the app (works offline). Behind a `Scorer` interface so `bge-small` can be swapped in. | **3 (onnxruntime, numpy, tokenizers)** |
| Clipboard | Python's `tkinter` clipboard call (built in) | none |
| Claude connection (optional add-on) | Official `mcp` Python SDK, shipped as a separate optional "Claude connector" so the core stays at two dependencies | add-on only |
| Build/packaging | PyInstaller (build-time only, not shipped to users as a dependency) | build only |

Runtime third-party dependencies: **five** (`pywebview`, `pypdf`, `onnxruntime`, `numpy`, `tokenizers`); the last three exist only for the embedding model. Expected bundle growth is roughly 100–150 MB per OS; confirm actual size and startup time in the first prototype. Caveat to verify in a first prototype: on Linux, pywebview needs the system GTK or Qt web-view package, so Linux may need a one-line install note or be a "run from source" target.

### Integrated browser: what it takes to embed real web pages
An ordinary `<iframe>` inside the app's HTML will **not** work: LinkedIn, Indeed and ZipRecruiter send headers (X-Frame-Options / frame-ancestors) that forbid being framed, and routing around that with a proxy would mean the app rewriting those sites' pages, which we won't do. Real options:

| Option | How it looks | Cost |
|---|---|---|
| **A. Side-docked companion window (v1 default)** | Two windows: the main app and a browser window. The app snaps them side by side and moves/resizes them together, with the address box, back/forward, and "Import from clipboard" in the main window. Feels integrated, not truly one window. | Free with pywebview; small amount of window-positioning code. |
| **B. Single window with an embedded browser (Qt)** | One window: left side the app's screens, right side a real embedded browser (QtWebEngine = bundled Chromium) with native address bar and buttons. Also removes the Linux system-package problem. | Swap pywebview for **PySide6**: still one dependency, but the download grows by roughly 250–350 MB per OS, and screens are built with Qt widgets or an embedded HTML view (a rewrite of the shell, not the logic). |
| C. Electron or Tauri shell | One window with embedded browser views. | Adds Node (Electron, ~150 MB Chromium) or Rust (Tauri, multi-webview still experimental) toolchains and a second language; conflicts with "few dependencies." |
| D. Native child-window embedding | Embeds the OS web view into another window. | Different fragile code per OS; not recommended. |

**Decision: build A now.** The browser pane lives behind a small `BrowserPane` interface, so **B** can replace it later without touching parsing, matching, or storage. **Reminder: experiment with implementing Option B after the v1 core is working** (see "Reminders").

## Distribution: portable on every OS
- **Portable = run without installing.** Each OS gets its own build, because the app is packaged per platform: Windows zip (folder + `.exe`), macOS `.app` (zipped/dmg), Linux AppImage. A GitHub Actions matrix builds all three. (Anyone with Python can also run it from source on any OS.)
- **Data lives in a `data/` folder next to the app**, so nothing is written to the registry or system folders. Deleting the folder removes the program and database (no uninstaller). Settings has "Delete all my data."
- The SQLite file format is identical across OSes, so a `data/` folder can be copied between a Windows and a Mac machine.
- Optional later: Windows installer with uninstaller.

## Search inputs
- Target **job titles** (multiple), expanded with synonyms from the vocabulary (e.g. "Software Engineer" ≈ "SDE").
- **Experience level** (multi-select): Entry / Junior, Mid, Senior, Lead / Manager. Effects: parses "years of experience" and level words ("Sr.", "Intern", "Principal") from listings; fit score penalizes level mismatch; tailoring and skills-gap advice are level-aware (entry → projects, coursework, internships; senior → scope, leadership, quantified impact).

## Getting listings (user-supplied only)
1. **Integrated browser:** a companion browser window the user drives. They open any job site, log in, browse and search normally (a helper can pre-fill the site's own search URL from titles/level/location). On a listing they select the text and copy (Ctrl+A, Ctrl+C), then click **"Import from clipboard"** in the main window. The app stores the clipboard text and the page address; it never reads the page's HTML and never clicks, scrolls, or navigates for the user.
2. **Paste text** into an import box, or **upload a file** (PDF/DOCX/TXT/HTML saved by the user).
3. De-duplication and a saved library of imported listings.

## Role profiles: keywords per position and level
- **Search context:** the main window has a "Currently searching for" selector (job title + experience level, e.g. "Data Analyst — Entry"). The user then runs that search on the job sites in the integrated browser themselves. Every listing they import is **automatically tagged** with the selected title and level.
- **Role keyword profile:** for each title+level pair the app aggregates its tagged listings into a profile: top required skills with "% of listings that ask for it," common nice-to-haves, typical years of experience, common certifications, and recurring phrases. This is the "what this role typically wants" source for tailoring advice and the skills-gap dashboard.
- **Confidence meter:** the profile shows how many listings it is built from ("7 listings — import ~10+ for a reliable profile") so users know when to trust it.
- **Cold start:** the starter vocabulary can include a few bundled generic profiles for common roles so the first import isn't empty. Bulk paste (several listings in one go, split by a separator) speeds up building a profile.
- **Readiness view:** before looking at any single listing, the app shows how well the resume covers the role profile ("62% of the skills entry-level Data Analyst listings ask for").
- A single listing is still scored against itself; the role profile adds context ("this listing asks for Tableau, which 70% of similar listings also ask for").

## Vocabulary and role profiles built from your listings (v1 approach; no embeddings needed)
- **Starter vocabulary:** a small bundled JSON file (a few hundred common skills, tools, certifications, soft skills, with aliases such as "JS" = "JavaScript"). It lets the very first listing work.
- **Learning from the library:** every listing the user saves goes into the database and becomes part of the corpus. After each import the app:
  1. Splits the listing into sections (Requirements / Nice to have / Responsibilities / About) by headings and phrases like "must have", "preferred".
  2. Mines **candidate skills**: frequent 1–3 word phrases in requirement lists, capitalized/technical tokens, terms not in the vocabulary that appear across several listings.
  3. Shows a short **"New terms found"** review (Add as skill / Merge into existing as alias / Ignore); confirmed terms join the vocabulary immediately and are re-applied to all saved listings.
- **Scoring with the corpus:** TF-IDF weights make rare, distinctive requirements count more than boilerplate words found in every listing; frequency across the user's *target-title* listings becomes the "importance" of a skill (this is also the data behind the skills-gap dashboard).
- Everything is explainable: the app can always show which exact words matched or were missing.

### Meaning layer (embedding model, always on)
- Resume bullets and each listing requirement line are turned into vectors by the bundled MiniLM model; vectors are cached in SQLite so each text is embedded once.
- For every requirement, the app finds the most similar resume bullet. Above a tuned threshold the requirement counts as **meaning-covered** ("Built REST services" ↔ "Backend API development") even with no shared keywords.
- The UI shows keyword matches and meaning matches separately (the matched resume bullet is displayed), so every score stays explainable. Coverage percentage = keyword-covered + meaning-covered requirements; the ring chart shades the two differently.
- Tailoring advice treats meaning-covered requirements as "you have this, but the posting's wording is X; consider mirroring it for ATS keyword filters."

## Core features (v1)
- Resume upload → parsed sections (contact, skills, experience, education) shown for correction; multiple resume versions.
- **Fit score** (0–100): keyword similarity + meaning match (embeddings) + required-skill coverage + title match + experience-level/years match; plain-English "why".
- **Required-skill coverage visual:** ring/donut chart with the percentage in the center ("80% — 12 of 15 required skills") plus color-coded chips (green matched, red missing, amber weakly shown); a thinner bar for nice-to-have coverage. Large on the listing page, small on best-fit rows.
- **Posting summary card:** role, company, location/remote, pay, level, must-haves vs nice-to-haves, key keywords, red flags (e.g. "10+ yrs for entry title", no pay listed, vague duties).
- **Resume tailoring report** (per listing): ranked keyword gaps, exact-phrase mirroring tips, where to add each (summary / skills / which bullet), level-appropriate emphasis; export tailored DOCX.
- **Resume format & readability review** (below).
- Ranked "best fits" list across saved listings.

### Resume format & readability review (ATS + human readers)
Each check has a plain-language "why it matters" and a fix:
- **Parseability (ATS):** multi-column layouts, tables, text boxes, contact info in headers/footers, images/icons/skill bars, unusual fonts, non-standard section names ("My Journey" → "Experience"), inconsistent date formats, file type (prefer DOCX or text-based PDF; flag scanned PDFs).
- **Human readability:** length for level (1 page entry, ≤2 senior), font size/margins/line spacing, consistent bullets, bullet length, wall-of-text detection, strong action verbs, quantified results, reverse-chronological order, scannable top third.
- **Layout/theme suggestions:** single-column layout and standard headings; 2–3 **ATS-safe export templates/themes** (e.g. Classic, Compact, Modern-minimal) with preview and before/after scores, suggested by level and field.
- Output: prioritized checklist (High / Medium / Nice-to-have) with separate "ATS readability" and "human readability" scores.

### Skills-gap dashboard
Aggregates every saved listing relative to the user's resume, titles, and level.
- **Gap ranking:** each missing skill with % of listings requiring it, required vs nice-to-have weighting, and **impact estimate** ("adding SQL would raise fit on 14 listings by ~6 pts on average").
- **Have-but-not-showing:** skills evidenced in experience text but absent/weak in the skills section or wording — quick wins.
- **Categories & filters:** hard skills, tools/tech, certifications, soft skills, domain knowledge; filter by level, location, company, date range.
- **Learning plan:** user marks each gap *Have / Learning / Not interested*; remaining gaps ordered by impact vs estimated effort with a short roadmap (curated free-resource search links per skill).
- **Trends:** skill demand across listings over time; rising skills highlighted.
- **Level-aware comparison:** entry vs senior target listings.
- **Export:** CSV/PDF summary.

## Other filters and features
Library filters: location, remote/hybrid/onsite, salary floor, date imported, job type, min fit score, exclude keywords/companies, visa sponsorship, security clearance. Extras: application tracker (status, dates, notes, follow-up reminders), stale-posting flag, cover-letter outline, tailored-resume version per listing, CSV export.

## Optional Claude connection (stretch, off by default)
Goal: use Claude for the things rules can't do well (fluent bullet rewrites, summaries, cover-letter drafts) with **no API key and no per-use cost**, using the user's existing Claude subscription.

### Mode 1 — Local MCP server (preferred)
- The app ships a tiny **MCP server** (a standard way for Claude Desktop or Claude Code to call tools on your computer). It runs only on the user's machine over stdio (no network port) and reads the same SQLite file. The user adds it once to Claude Desktop/Claude Code's config (the app has a "Connect to Claude" page that shows the exact snippet and the file path).
- **Tools exposed (read):** `get_resume`, `list_listings`, `get_listing`, `get_fit_report` (scores, coverage, gaps), `get_vocabulary`. **Tools exposed (write, narrow):** `save_suggestions(listing_id, …)` and `propose_vocabulary_terms(…)`; written items land in a **"Pending from Claude"** inbox in the app and nothing changes until the user accepts it. No tool can delete data or edit the resume directly.
- **Use:** the user opens Claude and says "Tailor my resume bullets to listing 12 without inventing anything" or "Which of my saved listings are the best fit for a junior data analyst?"; Claude reads via the tools, writes suggestions back, and the app shows them as side-by-side diffs with "not in your resume — verify" warnings on any new claim.
- **Honest trade-offs:** anything Claude reads through a tool becomes part of the conversation and is processed by Anthropic under the user's account terms (the user should check their plan's data/privacy settings); there is no way for Claude to *use* data it never receives. So the design minimizes what is sent instead (next list). It also needs Claude Desktop/Claude Code installed, is driven from Claude's chat rather than an in-app button, and is subject to the subscription's usage limits. Built with the official `mcp` SDK, packaged as a separate optional connector.
- **Keeping the resume away from Anthropic (privacy tiers, user picks in the app):**
  1. **Listings-only (default):** the connector exposes only saved listings (already public text), vocabulary, and app-computed results. Claude can summarize postings, rank them against a **skills profile** (just a list of skill names and years, no identifiers), and propose keywords. The resume itself is never exposed; the user applies suggestions to their own resume locally.
  2. **Anonymized resume (opt-in per section):** the tool returns resume bullets with name, email, phone, address, links, employer names, school names and exact dates replaced by placeholders ("Employer A", "University 1"); Claude's rewritten bullets come back with placeholders and are re-filled **locally**. Redaction is best-effort: project names or unusual details inside a bullet can still identify someone, so the preview shows exactly what would be served.
  3. **Full resume:** off unless the user explicitly enables it.
  - Every tool call and the exact payload served is written to a local **audit log** the user can read, and each tool has an on/off toggle. If the user wants zero data leaving the machine, they simply don't connect Claude, or use a local LLM later.

### Mode 2 — Copy/paste fallback (works with any chatbot)
"Get AI-written suggestions" shows a preview of exactly what will be copied (listing, chosen resume sections, app findings; user can untick sections or mask contact info), then **Copy prompt**. The user pastes it into any chatbot and pastes the reply into "Paste AI response"; the app validates it and shows the same accept/reject diff.

## Future ideas / to revisit
- **Email-alert scanning:** opt-in scan of the user's own mailbox for job-alert emails, queued for review. Needs checks before building: Gmail/Outlook OAuth read-only scopes (Gmail restricted-scope verification can be costly for a public release; IMAP with app passwords is simpler), each sender's terms, and a privacy design (read-only, local, selected senders only, easy disconnect). `.eml` upload as a lighter first step.
- **UI comparison to show later:** mockups of listing import: (A) integrated browser window with "Import from clipboard" vs (B) plain in-app viewer / user's own browser with paste only.
- **Embedding model reference (now built into v1; details kept for swapping to bge-small later):** a small sentence-embedding model (MiniLM, Apache-2.0) turns each resume bullet and listing sentence into a vector so "built web services" matches "backend development" even without shared words. It would plug in behind the `Scorer` interface, store vectors in the database, and appear as an extra "meaning match" column next to the keyword score. Cost: +~100–150 MB (ONNX runtime + quantized model) or +~0.5–1 GB (PyTorch), model download/licensing handling, 1–3 s cold start, per-OS packaging work, and scores that are harder to explain.
  - **What embeddings are:** a model converts a piece of text into a list of numbers (e.g. 384) that encodes its *meaning*; texts with similar meaning get similar numbers, so closeness of two lists (cosine similarity) measures similarity of meaning rather than shared words.
  - **What it takes (roughly 2–4 days):** (1) pick a small model (MiniLM or bge-small, ONNX, quantized ~25–90 MB); (2) add `onnxruntime`, `numpy`, `tokenizers` (or the `fastembed` library that bundles them) to the optional add-on; (3) bundle the model or download it on first run; (4) split resumes and listings into sentences/bullets and embed each; (5) store vectors as blobs in SQLite; (6) for each listing requirement find the most similar resume bullet and count it as "meaning-covered" above a tuned threshold; (7) show matched pairs in the UI next to keyword results; (8) tests and tuning thresholds on sample data; (9) per-OS packaging checks.
- Bring-your-own-API-key adapter, local-LLM (Ollama) adapter, Windows installer with uninstaller.

## Possible v2 additions (libraries left out of v1, and what each would enable)
Each is optional and would be added behind the existing interfaces (`Scorer`, `Tailorer`, `BrowserPane`) as a separate install so the core stays small.

### scikit-learn (small, most realistic first v2 step)
- **Role clustering:** group saved listings automatically ("these 12 'Analyst' listings are really two jobs: reporting vs data engineering").
- **Learned scoring:** replace fixed score weights with a simple model trained on the user's feedback (thumbs up/down, applied, got interview). Needs a feedback field on listings in v1's database.
- **Trained section/requirement detection:** classifier for must-have vs nice-to-have and section boundaries, improved by user corrections.
- **Near-duplicate / similar-listing detection:** catches reposts with small wording changes.
- **Threshold tuning:** cross-validation to set the meaning-match threshold objectively.
- Cost: pulls in `scipy` and `numpy` (~30–100 MB).

### PyTorch (only if fine-tuning or in-app generation is wanted)
- **Fine-tune the embedding model** on the user's feedback so meaning-matching becomes personal.
- **Heavier models in-app:** rerankers for more accurate resume–listing scoring, skill/title extractors that recognize terms outside the vocabulary, small summarization models for posting summaries, or a local text-generation model for bullet rewrites (llama.cpp/Ollama would still be the lighter route for generation).
- **GPU acceleration** where available.
- Cost: ~0.5–1 GB download and slower startup; `onnxruntime` already covers running finished models.

### Web framework (NiceGUI, Flask or FastAPI; only if remote/multi-device use is wanted)
- **Use from other devices:** run on a home PC, open from phone/laptop on the local network.
- **Live-updating screens:** progress bars during batch embedding, sortable/filterable/paged tables.
- **HTTP interface for integrations** (spreadsheet tools, other programs).
- **Faster UI development** with ready-made components.
- Cost: dozens of transitive packages and a local server port.

### v1 groundwork that keeps these easy
- Store a per-listing **feedback/outcome field** (liked, applied, interview, rejected) from the start.
- Keep scoring behind the `Scorer` interface and keep each score component (keyword, meaning, coverage, level) saved separately in the database so a learned model can use them as inputs.
- Keep logic out of the screens (`web/`) so a different UI layer can call the same Python functions.

## Project layout in plain language
Small folders ("rooms"), each with one job. Python files end in `.py`; no Python knowledge needed to follow this.

| Folder / file | What it is for | Analogy |
|---|---|---|
| `src/jobhelper/app.py` | Starts the program and opens the window | The front door |
| `src/jobhelper/web/` | The screens (HTML/CSS/JS): Resume, Listings, Listing detail, Skills dashboard, Tracker | The dining room |
| `src/jobhelper/browser/` | The companion browser window and "Import from clipboard" | A reading window you control |
| `src/jobhelper/parsing/resume.py` | Reads your PDF/DOCX and pulls out sections and skills | Reading glasses for the resume |
| `src/jobhelper/parsing/listing.py` | Turns pasted/clipboard/file text into a clean job record with sections | Reading glasses for postings |
| `src/jobhelper/matching/` | Vocabulary (starter + learned), term mining, TF-IDF, embedding-based meaning match, fit score, coverage percentages | The judge |
| `src/jobhelper/models/` | The bundled MiniLM model file (~23 MB) | The judge's training |
| `src/jobhelper/data/starter_vocabulary.json` | Starter list of skills and aliases | A starter dictionary |
| `src/jobhelper/tailoring/` | Keyword-gap advice, format/readability checks, resume templates and DOCX export | The editor |
| `src/jobhelper/dashboard/` | Skills-gap math, impact estimates, trends | The analyst |
| `src/jobhelper/storage/` | Saves resumes, listings, vocabulary, scores, tracker to a local database file | The filing cabinet |
| `src/jobhelper/mcp_server/` | (Optional) lets Claude read the database and leave suggestions | A guest window for Claude |
| `tests/` | Automated checks using sample resumes/listings | The safety inspector |
| `pyproject.toml` | The list of libraries and project settings | The recipe card |
| `.github/workflows/build.yml` | Builds portable packages for Windows, Mac, Linux automatically | The factory line |

Data flow: **Resume + Listing → parsing → matching (score + coverage) → tailoring (advice) → screens**; **storage** remembers everything, **matching** learns new vocabulary from saved listings, **dashboard** looks across all of them.

## Build order
1. **Prototype risks first:** pywebview window + companion browser window + clipboard read on Windows (and check Mac/Linux); load MiniLM through onnxruntime in a PyInstaller build and measure bundle size and cold-start time.
2. Skeleton, SQLite storage, resume parsing (PDF/DOCX), screen showing the parsed resume.
3. Titles + experience-level settings; listing import via paste/file/clipboard and the listing parser (sections, level, years, pay).
4. Starter vocabulary, term mining + "New terms found" review, TF-IDF, keyword fit score, coverage ring/chips; then the bundled embedding model with meaning matches shown beside keyword matches and folded into the fit score.
5. Posting summary card and tailoring report; DOCX export.
6. Resume format & readability review and ATS-safe templates.
7. Skills-gap dashboard; tracker; library filters.
8. Portable packaging (PyInstaller + CI matrix), `data/` folder behavior, "Delete all my data."
9. Stretch: MCP server, copy/paste mode.
10. **Remind the user to experiment with Option B** (embedded Qt browser) once step 8 is done; compare against Option A on feel, download size and Linux support.

## Reminders
- **Experiment with Option B (single-window embedded browser via PySide6/QtWebEngine)** after the v1 core is working. Planned trigger: when build step 8 finishes. The app's `ROADMAP.md` will carry this item, and Claude will raise it in the wrap-up of that step. Optionally, a scheduled reminder can be created after the plan is approved.

## Verification
- `pytest` with fixture resumes/listings: PDF+DOCX parsing, level/years extraction, section splitting, semantic matching (paraphrased requirement/bullet pairs outscore unrelated pairs; model loads offline from the bundled file), term mining (new terms appear after several listings), alias merging, coverage percentage math, scorer ordering (relevant listing outranks unrelated one; level mismatch lowers score), format-lint cases, gap-impact math; MCP server tool tests (read tools return data; write tools only add to the pending inbox).
- Run `python -m jobhelper`: upload resume → set titles + level → open a job site in the companion window → copy a listing → import from clipboard → see score, coverage ring, summary, tailoring and format reports → export DOCX → import ~10 listings and review "New terms found" and the dashboard. Confirm the app makes no network requests of its own to job sites.
- Build the Windows portable zip, unzip on a clean machine without Python, run, delete the folder, confirm nothing is left behind. Repeat build/launch check on macOS (and Linux if supported).
- If the MCP stretch is built: connect Claude Desktop/Code, run a tailoring request, confirm results appear in "Pending from Claude" and nothing changes until accepted.
