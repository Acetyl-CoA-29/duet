#!/usr/bin/env python3
"""Stand-in for both `claude -p` and `codex exec` in the comparison-harness tests. Never contacts
a model. It acts as Codex when its first argument is `exec`, otherwise as Claude.

Behaviour is set through environment variables:
  FAKE_EVAL_LOG       append one JSON line describing this call (argv, stdin, cwd, files seen, secrets)
  FAKE_EVAL_OVERLAY   copy this folder over the current directory (the "solution" the model wrote)
  FAKE_EVAL_OVERLAY_CODEX  the same, used instead when acting as Codex (so the two clients can differ)
  FAKE_EVAL_SLEEP     seconds to sleep before answering
  FAKE_EVAL_CHILD_PID start a long-lived grandchild and write its pid to this file
  FAKE_EVAL_EXIT      exit code
  FAKE_EVAL_STDERR    extra text printed on standard error
  FAKE_EVAL_REPLY     final message
  FAKE_EVAL_DUET      write this text to .duet/run-1/state.json (a duet that left a status behind)
  FAKE_EVAL_STATE_RUNS  write that state to <this folder>/run-1/state.json instead, with a "workspace"
                      field naming the current directory (a duet kept in the runner's state directory)
  FAKE_EVAL_TAMPER    overwrite this workspace-relative file with a test that always passes
"""
import json
import os
import shutil
import subprocess
import sys
import time

argv = sys.argv[1:]
prompt = sys.stdin.read()
is_codex = bool(argv) and argv[0] == "exec"

log = os.environ.get("FAKE_EVAL_LOG")
if log:
    seen = sorted(os.path.relpath(os.path.join(folder, name), ".")
                  for folder, _, names in os.walk(".") for name in names if ".git" + os.sep not in folder + os.sep)
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "argv": argv, "stdin": prompt, "cwd": os.getcwd(), "files": seen, "codex": is_codex,
            "secrets": sorted(k for k in os.environ if k.endswith(("_KEY", "_TOKEN"))),
        }) + "\n")

pid_file = os.environ.get("FAKE_EVAL_CHILD_PID")
if pid_file:
    grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    with open(pid_file, "w", encoding="utf-8") as handle:
        handle.write(str(grandchild.pid))

overlay = (os.environ.get("FAKE_EVAL_OVERLAY_CODEX") if is_codex else None) or os.environ.get("FAKE_EVAL_OVERLAY")
if overlay:
    shutil.copytree(overlay, ".", dirs_exist_ok=True)

tamper = os.environ.get("FAKE_EVAL_TAMPER")
if tamper:
    with open(tamper, "w", encoding="utf-8") as handle:
        handle.write("def test_nothing():\n    assert True\n")

duet_state = os.environ.get("FAKE_EVAL_DUET")
state_runs = os.environ.get("FAKE_EVAL_STATE_RUNS")
if duet_state:
    folder = os.path.join(state_runs, "run-1") if state_runs else os.path.join(".duet", "run-1")
    os.makedirs(folder, exist_ok=True)
    if state_runs:
        duet_state = json.dumps({**json.loads(duet_state), "workspace": os.getcwd()})
    with open(os.path.join(folder, "state.json"), "w", encoding="utf-8") as handle:
        handle.write(duet_state)

if is_codex:
    print(json.dumps({"type": "thread.started", "thread_id": "thread-fake"}), flush=True)
else:
    print(json.dumps({"type": "system", "subtype": "init", "session_id": "session-fake"}), flush=True)

time.sleep(float(os.environ.get("FAKE_EVAL_SLEEP", "0")))

code = int(os.environ.get("FAKE_EVAL_EXIT", "0"))
reply = os.environ.get("FAKE_EVAL_REPLY", "Done. The acceptance command passes.")
if os.environ.get("FAKE_EVAL_STDERR"):
    print(os.environ["FAKE_EVAL_STDERR"], file=sys.stderr)
if code:
    print("boom from the stand-in client", file=sys.stderr)
    sys.exit(code)

if is_codex:
    if "-o" in argv:
        with open(argv[argv.index("-o") + 1], "w", encoding="utf-8") as handle:
            handle.write(reply)
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": reply}}))
    print(json.dumps({"type": "turn.completed",
                      "usage": {"input_tokens": 1000, "cached_input_tokens": 400, "output_tokens": 50}}))
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 500, "output_tokens": 25}}))
else:
    print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": reply}]}}))
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": reply,
                      "session_id": "session-fake", "total_cost_usd": 0.4321, "duration_ms": 1234,
                      "usage": {"input_tokens": 2000, "cache_read_input_tokens": 9000, "output_tokens": 300}}))
