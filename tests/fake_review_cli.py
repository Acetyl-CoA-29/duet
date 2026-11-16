#!/usr/bin/env python3
"""Stand-in for both `claude -p` and `codex exec` in the review micro-benchmark tests. Never
contacts a model. It acts as Codex when its first argument is `exec`, otherwise as Claude.

Behaviour is set through environment variables:
  FAKE_REVIEW_LOG     append one JSON line describing this call (argv, stdin, cwd, files seen, secrets)
  FAKE_REVIEW_SCRIPT  a JSON file: {"default": "<reply>", "rules": [{...}, ...]}. The first rule whose
                      conditions all hold decides this call. Conditions: "contains" (text in the prompt),
                      "client" ("claude" or "codex"), "step" ("restate" or "review"). Effects: "reply",
                      "exit" (exit code), "cost" (dollars a Claude call reports), "no_session" (print no
                      session id), "sleep" (seconds).
"""
import json
import os
import sys
import time
import uuid

argv = sys.argv[1:]
prompt = sys.stdin.read()
is_codex = bool(argv) and argv[0] == "exec"
client = "codex" if is_codex else "claude"
step = "restate" if "Describe it only" in prompt else "review"
if is_codex:
    resumed = argv[-2] if len(argv) > 1 and argv[1] == "resume" else None
else:
    resumed = argv[argv.index("--resume") + 1] if "--resume" in argv else None

log = os.environ.get("FAKE_REVIEW_LOG")
if log:
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "argv": argv, "stdin": prompt, "cwd": os.getcwd(), "files": sorted(os.listdir(".")),
            "client": client, "step": step, "resumed": resumed,
            "secrets": sorted(k for k in os.environ if k.endswith(("_KEY", "_TOKEN"))),
        }) + "\n")

script = {}
if os.environ.get("FAKE_REVIEW_SCRIPT"):
    with open(os.environ["FAKE_REVIEW_SCRIPT"], encoding="utf-8") as handle:
        script = json.load(handle)


def applies(rule):
    return (rule.get("contains", "") in prompt and rule.get("client", client) == client
            and rule.get("step", step) == step)


rule = next((item for item in script.get("rules", []) if applies(item)), {})
reply = rule.get("reply", script.get("default", "DEFECTS: NONE"))
session = None if rule.get("no_session") else (resumed or f"sess-{uuid.uuid4().hex[:8]}")
time.sleep(float(rule.get("sleep", 0)))
if rule.get("exit"):
    print("boom from the stand-in reviewer", file=sys.stderr)
    sys.exit(int(rule["exit"]))

if is_codex:
    if session:
        print(json.dumps({"type": "thread.started", "thread_id": session}), flush=True)
    if "-o" in argv:
        with open(argv[argv.index("-o") + 1], "w", encoding="utf-8") as handle:
            handle.write(reply)
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": reply}}))
    print(json.dumps({"type": "turn.completed",
                      "usage": {"input_tokens": 1200, "cached_input_tokens": 200, "output_tokens": 80}}))
else:
    result = {"type": "result", "subtype": "success", "is_error": False, "result": reply,
              "total_cost_usd": float(rule.get("cost", 0.05)),
              "usage": {"input_tokens": 900, "cache_read_input_tokens": 3000, "output_tokens": 70}}
    if session:
        result["session_id"] = session
    print(json.dumps(result))
