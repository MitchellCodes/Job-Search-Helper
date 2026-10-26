"""Run Claude Code headless (`claude -p`) as one step: text in, validated JSON out.

The app builds the prompt, so it controls exactly what is sent. Claude is started with every tool
turned off, in an empty temporary folder, with no saved session and no slash commands or MCP
servers, so it cannot read files or reach anything on this computer; it can only answer.
It uses the user's existing Claude Code sign-in (no API key).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

COMMAND_ENV = "JOBHELPER_CLAUDE_CMD"   # JSON list, e.g. ["python", "fake_claude.py"] (tests / development)
DEFAULT_TIMEOUT = 300


class ClaudeUnavailable(Exception):
    """Claude Code isn't installed or can't be found."""


class ClaudeFailed(Exception):
    """Claude ran but didn't give a usable answer. The message is safe to show the user."""


def _creation_flags() -> int:
    return subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0   # no console flash from the windowed app


def find_claude() -> list[str] | None:
    override = os.environ.get(COMMAND_ENV)
    if override:
        return list(json.loads(override))
    found = shutil.which("claude")
    if found:
        return [found]
    home = Path.home()
    for candidate in (home / ".local" / "bin" / "claude.exe", home / ".local" / "bin" / "claude",
                      home / ".claude" / "local" / "claude"):
        if candidate.exists():
            return [str(candidate)]
    return None


def status() -> dict:
    """Is Claude Code available? (Doesn't check sign-in; a real request reports that.)"""
    command = find_claude()
    if not command:
        return {"available": False, "version": None}
    try:
        out = subprocess.run(command + ["--version"], capture_output=True, text=True, timeout=20,
                             stdin=subprocess.DEVNULL, creationflags=_creation_flags())
        return {"available": out.returncode == 0, "version": out.stdout.strip() or None}
    except (OSError, subprocess.TimeoutExpired):
        return {"available": False, "version": None}


def _friendly(problem: str) -> str:
    low = problem.lower()
    if "login" in low or "not logged in" in low or "authenticat" in low or "api key" in low:
        return "Claude Code isn't signed in. Open a terminal, run `claude`, sign in, then try again."
    if "limit" in low or "rate" in low or "overloaded" in low or "quota" in low:
        return f"Claude is busy or your usage limit was reached: {problem.strip()[:200]}"
    return f"Claude couldn't complete the request: {problem.strip()[:300]}"


def run_claude(user_prompt: str, schema: dict, system_prompt: str, model: str | None = None,
               timeout: int = DEFAULT_TIMEOUT, command: list[str] | None = None) -> dict:
    """Returns {"data": <object matching schema>, "seconds": float, "cost_usd": float|None, "model": str|None}."""
    base = command or find_claude()
    if not base:
        raise ClaudeUnavailable("Claude Code wasn't found. Install it from claude.com/claude-code and sign in, "
                                "then try again.")
    args = base + ["-p", "--output-format", "json", "--json-schema", json.dumps(schema),
                   "--tools", "", "--no-session-persistence", "--disable-slash-commands", "--strict-mcp-config",
                   "--system-prompt", system_prompt]
    if model:
        args += ["--model", model]
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="jobhelper_claude_") as empty_dir:   # no project files, no CLAUDE.md
        try:
            proc = subprocess.run(args, input=user_prompt, capture_output=True, text=True, encoding="utf-8",
                                  cwd=empty_dir, timeout=timeout, creationflags=_creation_flags())
        except FileNotFoundError as e:
            raise ClaudeUnavailable("Claude Code couldn't be started. Check that it is installed.") from e
        except subprocess.TimeoutExpired as e:
            raise ClaudeFailed(f"Claude took longer than {timeout} seconds. Try again.") from e
    seconds = round(time.time() - started, 1)

    try:
        reply = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        raise ClaudeFailed(_friendly(proc.stderr or proc.stdout or f"exit code {proc.returncode}"))
    if proc.returncode != 0 or reply.get("is_error"):
        raise ClaudeFailed(_friendly(str(reply.get("result") or proc.stderr or reply.get("subtype") or "unknown error")))

    data = reply.get("structured_output")
    if not isinstance(data, dict):
        try:
            data = json.loads(reply.get("result") or "")
        except (json.JSONDecodeError, TypeError):
            data = None
    if not isinstance(data, dict):
        raise ClaudeFailed("Claude's reply wasn't in the expected format. Try again.")
    models = reply.get("modelUsage") or {}
    return {"data": data, "seconds": seconds, "cost_usd": reply.get("total_cost_usd"),
            "model": next(iter(models), None)}
