#!/usr/bin/env python3
"""Stand-in for `claude -p` used by the test suite. Never contacts a model."""
import json
import os
import sys

argv = sys.argv[1:]
stdin = sys.stdin.read()
log = os.environ.get("FAKE_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"argv": argv, "stdin": stdin, "cwd": os.getcwd(),
                             "secrets": sorted(k for k in os.environ if k.endswith(("_KEY", "_TOKEN")))}) + "\n")
print(json.dumps({
    "type": "result",
    "is_error": bool(os.environ.get("FAKE_IS_ERROR")),
    "result": os.environ.get("FAKE_REPLY", "Looks right.\nVERDICT: APPROVE"),
    "session_id": os.environ.get("FAKE_SESSION", "claude-sess-9"),
    "total_cost_usd": float(os.environ.get("FAKE_COST", "0.25")),
    "usage": {"input_tokens": 50, "output_tokens": 5},
}))
