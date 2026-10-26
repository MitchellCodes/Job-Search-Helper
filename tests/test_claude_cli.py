import json
import sys
from pathlib import Path

import pytest

from jobhelper.tailoring import claude_cli
from jobhelper.tailoring.claude_cli import ClaudeFailed, ClaudeUnavailable, run_claude

FAKE = [sys.executable, str(Path(__file__).parent / "fake_claude.py")]
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


@pytest.fixture()
def fake(tmp_path, monkeypatch):
    log = tmp_path / "log.json"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.delenv("FAKE_CLAUDE_MODE", raising=False)
    monkeypatch.delenv("FAKE_CLAUDE_REPLY", raising=False)
    return log


def test_returns_structured_data_and_metadata(fake):
    out = run_claude("hello [NAME]", SCHEMA, "system words", command=FAKE)
    assert out["data"] == {"ok": True}
    assert out["cost_usd"] == 0.01 and out["model"] == "fake-model" and out["seconds"] >= 0


def test_claude_is_locked_down_and_gets_only_the_prompt(fake):
    run_claude("PROMPT-TEXT", SCHEMA, "SYSTEM-TEXT", model="some-model", command=FAKE)
    seen = json.loads(fake.read_text(encoding="utf-8"))
    args = seen["args"]
    assert seen["stdin"] == "PROMPT-TEXT"                        # data goes via stdin, not the command line
    assert "PROMPT-TEXT" not in " ".join(args)
    assert args[args.index("--tools") + 1] == ""                 # every tool disabled
    for flag in ("-p", "--no-session-persistence", "--disable-slash-commands", "--strict-mcp-config"):
        assert flag in args
    assert args[args.index("--system-prompt") + 1] == "SYSTEM-TEXT"
    assert args[args.index("--model") + 1] == "some-model"
    assert json.loads(args[args.index("--json-schema") + 1]) == SCHEMA
    assert seen["cwd_entries"] == []                             # an empty folder: no project files to read
    assert "jobhelper_claude_" in seen["cwd"]


def test_model_flag_omitted_by_default(fake):
    run_claude("x", SCHEMA, "s", command=FAKE)
    assert "--model" not in json.loads(fake.read_text(encoding="utf-8"))["args"]


@pytest.mark.parametrize("mode, expected", [
    ("badjson", "Claude couldn't complete"),
    ("error", "Something broke"),
    ("login", "isn't signed in"),
    ("limit", "usage limit"),
    ("noschema", "expected format"),
])
def test_failures_become_readable_messages(fake, monkeypatch, mode, expected):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
    with pytest.raises(ClaudeFailed, match=expected):
        run_claude("x", SCHEMA, "s", command=FAKE)


def test_timeout_is_reported(fake, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "slow")
    with pytest.raises(ClaudeFailed, match="took longer than 2 seconds"):
        run_claude("x", SCHEMA, "s", timeout=2, command=FAKE)


def test_missing_claude_is_reported(monkeypatch):
    monkeypatch.delenv(claude_cli.COMMAND_ENV, raising=False)
    monkeypatch.setattr(claude_cli, "find_claude", lambda: None)
    with pytest.raises(ClaudeUnavailable, match="wasn't found"):
        run_claude("x", SCHEMA, "s")
    with pytest.raises(ClaudeUnavailable):
        run_claude("x", SCHEMA, "s", command=["definitely-not-a-real-program-xyz"])


def test_status_reports_version(monkeypatch):
    monkeypatch.setenv(claude_cli.COMMAND_ENV, json.dumps(FAKE))
    assert claude_cli.status() == {"available": True, "version": "9.9.9 (Fake Claude)"}
    monkeypatch.setattr(claude_cli, "find_claude", lambda: None)
    assert claude_cli.status() == {"available": False, "version": None}
