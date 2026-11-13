#!/usr/bin/env python3
"""Stand-in for `codex exec` used by the test suite. Never contacts a model."""
import json
import os
import sys
import time

argv = sys.argv[1:]
stdin = sys.stdin.read()
log = os.environ.get("FAKE_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"argv": argv, "stdin": stdin, "cwd": os.getcwd(),
                             "secrets": sorted(k for k in os.environ if k.endswith(("_KEY", "_TOKEN")))}) + "\n")
print(json.dumps({"type": "thread.started", "thread_id": os.environ.get("FAKE_SESSION", "sess-123")}), flush=True)
time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
code = int(os.environ.get("FAKE_EXIT", "0"))
if code:
    print("boom from fake peer", file=sys.stderr)
    sys.exit(code)
reply = os.environ.get("FAKE_REPLY", "Agreed.\nVERDICT: APPROVE")
touched = os.environ.get("FAKE_WRITE_FILE")
if touched:
    with open(touched, "a", encoding="utf-8") as fh:
        fh.write("edit\n")
if "-o" in argv and not os.environ.get("FAKE_NO_LAST_FILE"):
    with open(argv[argv.index("-o") + 1], "w", encoding="utf-8") as fh:
        fh.write(reply)
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": reply}}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 7}}))
