"""A stand-in for `claude -p` used in tests. It records what it was given and replies per FAKE_CLAUDE_MODE.

It never contacts anything. Tests read FAKE_CLAUDE_LOG to check exactly which arguments and text the
app sent, which is how the privacy guarantee is verified.
"""
import json
import os
import sys

mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
log = os.environ.get("FAKE_CLAUDE_LOG")
if "--version" in sys.argv:          # like the real CLI, answer without reading stdin
    print("9.9.9 (Fake Claude)")
    sys.exit(0)
stdin_text = sys.stdin.read()
if log:
    with open(log, "w", encoding="utf-8") as f:
        json.dump({"args": sys.argv[1:], "stdin": stdin_text, "cwd": os.getcwd(),
                   "cwd_entries": os.listdir(os.getcwd())}, f)

reply_file = os.environ.get("FAKE_CLAUDE_REPLY")      # path to a JSON file holding the structured answer
if mode == "badjson":
    print("this is not json")
elif mode == "error":
    print(json.dumps({"is_error": True, "subtype": "error_during_execution", "result": "Something broke"}))
    sys.exit(1)
elif mode == "login":
    print(json.dumps({"is_error": True, "result": "Not logged in · Please run /login"}))
    sys.exit(1)
elif mode == "limit":
    print(json.dumps({"is_error": True, "result": "You have reached your usage limit"}))
    sys.exit(1)
elif mode == "noschema":
    print(json.dumps({"is_error": False, "result": "plain words, no structure"}))
elif mode == "slow":
    import time
    time.sleep(30)
else:
    structured = json.load(open(reply_file, encoding="utf-8")) if reply_file else {"ok": True}
    print(json.dumps({"is_error": False, "structured_output": structured, "result": json.dumps(structured),
                      "total_cost_usd": 0.01, "modelUsage": {"fake-model": {}}}))
