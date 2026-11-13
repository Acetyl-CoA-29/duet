# duet comparison harness

Answers one question: at the same budget, does a duet do better than one model working alone?

It runs the same task through several **arms**, each in its own throwaway workspace, then scores
every finished workspace twice: with the visible acceptance command the arm was given, and with
a hidden oracle test suite the arm never saw. The number that matters is how often work that
*looked* done (visible command passes) was actually wrong (oracle fails).

Nothing here imports the duet runner. Duet arms start a host agent headless and tell it to use
the skill, the way a user would.

## Arms

| Arm | What runs |
|---|---|
| `solo-claude` | `claude -p`, told to iterate until the acceptance command passes and to review its own work |
| `solo-codex` | `codex exec`, same instructions |
| `duet-claude-host` | `claude -p` as host, told to read `SKILL.md` and run a duet with Codex as peer |
| `duet-codex-host` | `codex exec` as host, with Claude as peer (`--peer claude`) |

Every arm gets the same task text, acceptance command and rules, word for word
(`arms.task_brief`). Arms live in one table, `ARMS` in `evallib/arms.py`: a name mapped to a
function that returns the command and the prompt. Add a best-of-two or reviewer-only arm by
writing one more function and registering it.

How each client is started:

- **Claude** (either role): `--safe-mode` (no personal hooks, instructions or plugins),
  `--permission-mode acceptEdits`, and its own command sandbox with no network, so it can run
  tests without approval prompts. A Claude **host** gets three narrow additions: the duet
  runner (`python3 <skill>/scripts/duet.py ...`), and only that command, is allowed and runs
  outside the sandbox so it can reach Codex; it may read `SKILL.md`; and it may read and write
  the runner's state folder (`$DUET_HOME`, default `~/.local/state/duet`), where the skill tells
  it to put its message drafts. No bypass flags are used. Safe mode turns skill discovery off,
  which is why the prompt names the `SKILL.md` path.
- **Codex** (either role): `workspace-write` sandbox. A Codex **host** also gets
  `sandbox_workspace_write.network_access=true`, `--add-dir ~/.claude` and, when it exists,
  `--add-dir` for the runner's state folder, as the skill requires for reaching the Claude
  client. Codex runs with your normal Codex configuration, so your own Codex hooks and
  instructions run in both Codex arms and are part of what is measured.
- Duet hosts are told to pass `--timeout 540` (or the run's limit, if shorter) to every peer
  turn, and to follow the skill's own retry rules when a runner command fails.
- Prompts go in on standard input. The other lab's API key and common deploy secrets are removed
  from each client's environment, so the duet peer must be signed in through its own client.

## Use

```sh
cd ~/.agents/skills/duet

python3 eval/run_eval.py list                      # tasks and arms
python3 eval/run_eval.py verify                    # check the tasks themselves; no model calls

# see exactly what would run, without running it
python3 eval/run_eval.py run --tasks duration --arms all --dry-run

# one task, all four arms, one seed
python3 eval/run_eval.py run --tasks duration --arms all --timeout 600 --max-cost 6 --effort low

# the full matrix (expensive: see "Cost" below)
python3 eval/run_eval.py run --tasks all --arms all --seeds 3 --max-total-cost 150

python3 eval/run_eval.py summary                   # every session under the results root
python3 eval/run_eval.py summary --results ~/.duet-eval/results/<session> --by-task
```

`--tasks` and `--arms` are required, so nothing runs that you did not name; `all` is explicit.

| Option | Meaning |
|---|---|
| `--seeds N` | repetitions of each (task, arm). Default 1. The clients take no random seed; a "seed" is a fresh repetition. |
| `--timeout S` | wall-clock limit per run (default 900). On expiry the client's whole process group is stopped. |
| `--max-cost D` | dollars per run (default 6). Enforced where a client can: `--max-budget-usd` for a Claude host or solo, the duet's own `--max-cost` for a Claude peer. Codex reports no dollars, so only the timeout caps it. |
| `--max-total-cost D` | stop starting new runs once reported spend reaches this. Counts reported dollars only. |
| `--effort E` | reasoning effort for both clients, and `--peer-effort` for a Codex peer. |
| `--duet-turns N` | peer turn cap for duet arms (default 8). |
| `--dry-run` | print each command and stop. `--show-prompt` adds the prompts. |
| `--results DIR` | results root, default `~/.duet-eval/results`. Refused if it is inside a git repository. |
| `--keep-workspaces` | keep finished workspaces instead of deleting them. |

## Output

```
<results>/<session>/
  records/<task>__<arm>__s<seed>.json    one record per run
  logs/<task>__<arm>__s<seed>/           prompt.md, command.json, stdout.txt, stderr.txt,
                                         diff.patch, visible.log, hidden.log,
                                         duet/ (the workspace's .duet folder), duet-run/ (a copy
                                         of the run folder the status report names)
  work/                                  throwaway workspaces, removed after scoring
```

A record holds: `visible` and `hidden` (pass/fail, exit code, test counts where pytest printed
them), `visible_pass_hidden_fail`, `seconds`, `cost_usd` (reported dollars; `host_cost_usd` and
`peer_cost_usd` separately), `tokens`, `status` (`ok`, `error`, `timeout`, `not_started`),
`exit_code`, `tampered` (protected test files the arm changed in any way, additions included;
the originals are what the visible score runs), `peer_tokens`, `oracle_paths_seen`, and for duet arms `duet.finished`: `true`, `false`, or
`"unknown"` when no status report could be found. That last field is best-effort: it reads the
status JSON the host is asked to print, then falls back to any JSON file with a `status` field
under the workspace's `.duet/`.

`summary` prints, per arm: hidden pass rate, visible pass rate, the looked-done-but-wasn't rate,
duets finished, mean reported dollars, mean tokens (input including cached, plus output, plus
the duet peer's where its status reported them), mean seconds. When
a run is repeated, the most recent record for that (task, arm, seed) is the one counted.

## Tasks

A task is a directory:

```
task.md               the goal given to the model
task.json             {"accept": "<visible command>", "protected": ["tests"], "hidden_cmd": "<optional>"}
accept.sh             alternative to "accept": copied into the workspace and run with sh
starter/              copied into a fresh workspace
hidden/               oracle tests: never copied into a workspace
solutions/reference/  overlay that must pass everything      (used only by `verify`)
solutions/naive/      overlay that passes visible, fails hidden (used only by `verify`)
```

After the arm's process has ended, the workspace is copied, the `protected` files are restored
from the starter (so weakened tests do not count), the visible command runs, then `hidden/` is
added to the copy as `hidden_tests/` and the oracle runs.

| Task | Kind | The trap |
|---|---|---|
| `slugify` | edge cases | accents, apostrophes, truncation that leaves a trailing hyphen, empty results |
| `duration` | input validation | a find-all regex accepts garbage, wrong order, repeats, decimals, non-ASCII digits |
| `split-bill` | subtle spec | who gets the leftover cents, ties, refunds, exactness on huge totals |
| `ttl-cache-bug` | debugging | the failing test points at one seeded defect; the class breaks its contract in several more places |
| `invoice-refactor` | refactor | a tidy rewrite drops rounding mode, discount fallback, sort order, returns; scored by differential test against the original |

Every trap is stated in `task.md`; the visible tests simply do not exercise it.
`run_eval.py verify` proves, without any model, that for each task the starter fails, the
reference passes both suites, and the naive solution passes visible and fails hidden.

## Limits to keep in mind when reading results

- **Dollars are not comparable across labs.** Claude reports dollars, Codex reports tokens. A
  `duet-claude-host` cost omits the Codex peer; a `duet-codex-host` cost is the Claude peer
  only; `solo-codex` has none. Compare seconds and tokens too, or price the tokens yourself.
- **"Same budget" means the same caps**, not the same spend. A duet that uses its whole cap is
  being compared with a solo run that may stop early. A fairer contest for the duet's extra
  spend is a best-of-two solo arm; the arm table is built so one can be added.
- **The oracle is hidden from the workspace, not from the disk.** The task folders sit in this
  checkout, which a duet host is pointed at. Workspace paths do not name the task, and each
  record lists `oracle_paths_seen` (task-folder paths found in the client's output or the duet
  transcript) so a contaminated run can be thrown out. For a stricter setup keep tasks
  elsewhere and pass `--tasks-dir`.
- **Five small Python tasks** are a smoke-sized benchmark. With one seed, a difference of one
  task is noise. Use several seeds before believing a gap, and add tasks closer to your real
  work.
- A Claude host's shell tool stops a command after ten minutes, so a slow peer turn can cut a
  duet short where an interactive session would not.

## Tests

```sh
python3 -m pytest tests/test_eval.py -q
```

Stand-in clients only (`tests/fake_eval_cli.py`); no model is called.
