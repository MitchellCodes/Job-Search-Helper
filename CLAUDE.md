# CLAUDE.md

Local, portable job-search helper. Read `README.md` first: it explains the **split between the models and
the code**, which is the point of the project. Keep that boundary.

## Models vs. code (keep this boundary)

Two models, narrow jobs:

1. **Claude, via headless Claude Code** (`tailoring/claude_cli.py`, prompt and checks in `tailoring/tailor.py`).
   Its one job: propose resume edits as JSON for one listing. It never decides what gets applied.
2. **MiniLM** (`matching/embeddings.py`, local ONNX). Its one job: text to vectors for the meaning match.
   Decisions on its numbers live in `matching/scorer.py`. If it can't load, scoring still works on keywords.

All checks are plain code. When changing the tailoring flow, keep every one of these:
- Redact first (`privacy/redact.py`), then **leak-check**; if anything is found, refuse to send (`PrivacyBlocked`).
  The header block (name/contact) is never sent. The user sees the exact text before sending.
- Start Claude with **`--tools ""`**, an **empty temp working directory**, `--no-session-persistence`,
  `--disable-slash-commands`, `--strict-mcp-config`, data via **stdin** (never the command line).
  `tests/test_claude_cli.py` asserts all of this against `tests/fake_claude.py`; don't weaken it.
- Never trust Claude's text: validate (`validate_and_restore`): quoted originals must exist in the resume;
  skills need verbatim evidence; new numbers/terms flagged; unknown placeholders block; the Word export is built
  from the user's own text plus only the edits the user accepted.
- Don't use `--bare` blindly: untested with subscription sign-in. The current flags were verified with a real call.

## Hard rules from the user

- **Never scrape, crawl or auto-fetch any job site; no job-site APIs.** The user pastes each listing.
- No paid API key. Keep runtime dependencies minimal (currently 5). No scikit-learn, PyTorch, web framework.
- Resumes: PDF and DOCX only. No OCR/screenshots. No integrated browser or clipboard reading (removed on purpose).
- Personal info removed before sending to Claude and restored after: name, email, phone, locations, profile links,
  employer, school, project and certificate names.
- Don't commit or push unless asked.

## Commands

- Tests: `.venv\Scripts\python -m pytest`. Real-Claude test (uses the user's usage): `JOBHELPER_TEST_CLAUDE=1 ... tests/test_claude_live.py`
- Run: `.venv\Scripts\python -m jobhelper` ; browser-test mode: `... -m jobhelper --dev --port 8765`
- To try the UI without using Claude: set `JOBHELPER_CLAUDE_CMD='["python","tests/fake_claude.py"]'` and
  `FAKE_CLAUDE_REPLY=<json file>` before `--dev`.
- Build portable exe: `powershell -ExecutionPolicy Bypass -File scripts\build_portable.ps1 -Smoke`

## Lessons learned (things that bit us)

- **Don't rebuild `dist\` while the app is open** (Windows locks files). Use the build script: it keeps and
  verifies the user's `dist\JobSearchHelper\data` folder. Never delete that folder.
- **Don't patch code through Python/heredoc string replacement with `\n`, `\b` or `\[` in it.** It silently
  produced real newlines/backspaces repeatedly. Use the Edit/Write tools; run the tests or `ast.parse` after.
- `.gitignore` uses `/data/` (not `data/`): `src/jobhelper/data/starter_vocabulary.json` must stay tracked.
- Saved listings store parsed data; when parsing rules change, bump `PARSER_VERSION` in `parsing/listing.py`.
- Resume text from DOCX and PDF differs. DOCX bullets have no bullet character; PDF extraction splits bold/regular
  runs into separate lines, puts dates on their own line, wraps bullets, and renders bullets as `\x7f`.
  `tailoring/layout.py` (`merge_fragments`, `classify`) normalises both; use it, don't re-parse lines yourself.
- A child process must never inherit the app's stdin (`stdin=DEVNULL`); a stand-in that waits for stdin hangs.
- Slow calls (Claude takes ~10 s+) must not hold the API lock: see `Api.tailor_run` (lock only around DB steps).
- pywebview defaults: `text_select=False` blocks selecting/copying text; the right-click menu is debug-only
  (`app.enable_context_menu` turns it on for WebView2). `evaluate_js` doesn't await promises.
- Python 3.15 lacks wheels for `onnxruntime`/`tokenizers`; use 3.12 or 3.13.

## Testing notes

- Tests needing MiniLM are skipped if `src/jobhelper/models/model_quantized.onnx` is missing.
- Fixtures: `tests/test_matching.py` (sample listings), `samples/` (fictional resume as DOCX and PDF; both
  are exercised by `tests/test_tailoring.py`).
- Only Windows has been tested.
