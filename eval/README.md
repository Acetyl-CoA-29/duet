# duet comparison harness

Built to answer one question: under comparable limits, does a duet do better than one model
working alone, or than two models working apart?

It runs the same task through several **arms**, each in its own throwaway workspace, then scores
every finished workspace twice: with the visible acceptance command the arm was given, and with
a hidden oracle test suite the arm never saw. The number that matters is how often work that
*looked* done (visible command passes) was actually wrong (oracle fails).

Nothing here imports the duet runner. Duet arms start a host agent headless and tell it to use
the skill, the way a user would.

## What a run can and cannot tell you

Read this before spending money on a matrix.

A run **can** support:

- how often each arm's finished work passes an oracle it never saw, on these tasks, with a
  95% interval beside every rate;
- how often work that looked done (visible command passes) was wrong (oracle fails);
- how often a duet actually took place and finished, and what it cost in time and reported
  dollars compared with one model alone.

A run **cannot** support:

- "the duet is better" or "worse" from a gap whose intervals overlap. Small task sets produce
  wide intervals; report that uncertainty instead of ranking arms from point estimates alone.
- anything about work unlike these tasks. Every arm passed the historical five-task smoke set
  of small single-file Python tasks. The current nine-task set adds multi-file repairs whose
  contracts span code and documentation; the old ceiling result does not measure that set.
- a like-for-like price. Claude reports dollars and Codex reports tokens, and the two token
  counts are not the same quantity.
- "same budget". Arms get the same caps, scaled to their structure, not the same spend.

## Arms

| Arm | What runs |
|---|---|
| `solo-claude` | `claude -p`, told to iterate until the acceptance command passes |
| `solo-codex` | `codex exec`, same instructions |
| `duet-claude-host` | `claude -p` as host, told to read `SKILL.md` and run a duet with Codex as peer |
| `duet-codex-host` | `codex exec` as host, with Claude as peer (`--peer claude`) |
| `best-of-two-solo` | `solo-claude` and `solo-codex`, each in its own workspace on half the time and half the dollar cap; one result is kept |

`best-of-two-solo` is the baseline for "two models, no conversation". The kept result is the
one that passes the visible command; if both or neither do, the one that passed more visible
tests, then the smaller diff, then Claude's. The oracle plays no part in the choice.

Every arm gets the same task text, acceptance command, rules and quality bar, word for word
(`arms.task_brief`): reread the task line by line, check each requirement, try a case the tests
do not cover. The only thing that differs between arms is the working method. Arms live in one
table, `ARMS` in `evallib/arms.py`; arms made of other arms go in `COMPOSITE_ARMS`.

How each client is started:

- **Claude** (either role): `--safe-mode` (no personal hooks, instructions or plugins),
  `--permission-mode acceptEdits`, and its own command sandbox with no network, so it can run
  tests without approval prompts. Reading the results folder and the task source folder is
  denied by rule. A Claude **host** gets three narrow additions: the duet runner
  (`python3 <skill copy>/scripts/duet.py ...`), and only that command, is allowed and runs
  outside the sandbox so it can reach Codex; it may read `SKILL.md`; and it may read and write
  the runner's state folder (`$DUET_HOME`, default `~/.local/state/duet`), where the skill tells
  it to put its message drafts. No bypass flags are used. Safe mode turns skill discovery off,
  which is why the prompt names the `SKILL.md` path.
- **Codex** (either role): `workspace-write` sandbox. A Codex **host** also gets
  `sandbox_workspace_write.network_access=true`, `--add-dir ~/.claude` and, when it exists,
  `--add-dir` for the runner's state folder, as the skill requires for reaching the Claude
  client. Codex runs with your normal Codex configuration, so your own Codex hooks and
  instructions run in every Codex arm and are part of what is measured. Codex's sandbox allows
  reading the whole disk; see "Leaks" for what that means.
- Duet hosts are told to pass `--timeout 540` (or the run's limit, if shorter) to every peer
  turn, and to follow the skill's own retry rules when a runner command fails.
- Prompts go in on standard input. The other lab's API key and common deploy secrets are removed
  from each client's environment, so the duet peer must be signed in through its own client.

### Limits per arm

| | wall clock | dollars | effort |
|---|---|---|---|
| solo | `--timeout` (default 900 s) | `--max-cost` for Claude; none for Codex | `--effort` |
| duet | `--timeout × (1 + turns / 2)`: one solo allowance for the host's own work plus half of one per peer turn allowed (4500 s by default). Override with `--duet-timeout` | `--max-cost` on the Claude side (host or peer); none on the Codex side | host: `--effort`. Codex peer: `--peer-effort`. **Claude peer: its own default**, because the duet runner has no effort option for a Claude peer |
| best-of-two | half of `--timeout` per member | half of `--max-cost` for the Claude member | `--effort` |

A duet waits on a second model turn after turn; on a solo run's limit it would fail on time
alone, which is why its limit is scaled. `run` and `--dry-run` print the limits they use.

## Use

```sh
cd ~/.agents/skills/duet

python3 eval/run_eval.py list                      # tasks and arms
python3 eval/run_eval.py verify                    # check the tasks themselves; no model calls

# see exactly what would run, without running it
python3 eval/run_eval.py run --tasks duration --arms all --dry-run

# one task, all five arms, one seed
python3 eval/run_eval.py run --tasks duration --arms all --timeout 600 --max-cost 6 --effort low

# the shipped matrix (see "Cost" below before running it)
python3 eval/run_eval.py run --tasks all --arms all --seeds 3 --max-total-cost 150

python3 eval/run_eval.py summary                   # one table per session under the results root
python3 eval/run_eval.py summary --results ~/.duet-eval/results/<session> --by-task
python3 eval/run_eval.py summary --merge           # one table over every session, if their limits match
```

`--tasks` and `--arms` are required, so nothing runs that you did not name; `all` is explicit.

| Option | Meaning |
|---|---|
| `--seeds N` | repetitions of each (task, arm). Default 1. The clients take no random seed; a "seed" is a fresh repetition. |
| `--order-seed N` | the arms of each (task, seed) run in an order shuffled from this seed, so no arm always runs last. Default: a fresh seed, printed and recorded. |
| `--timeout S` | wall-clock limit for a solo run (default 900). Other arms derive theirs from it (table above). On expiry the client's whole process group is stopped. |
| `--duet-timeout S` | wall-clock limit for a duet run, instead of the derived one. |
| `--max-cost D` | dollars per run (default 6). Enforced where a client can: `--max-budget-usd` for a Claude host or solo, the duet's own `--max-cost` for a Claude peer. Codex reports no dollars, so only the timeout caps it. |
| `--max-total-cost D` | stop starting new runs once reported spend reaches this. Counts reported dollars only. A session stopped this way is flagged as unbalanced. |
| `--effort E` | reasoning effort for every client that takes one (see the table above for the one that does not). |
| `--duet-turns N` | peer turn cap for duet arms (default 8). |
| `--dry-run` | print each command and stop. `--show-prompt` adds the prompts. |
| `--results DIR` | results root, default `~/.duet-eval/results`. Refused if it is inside a git repository. |
| `--work-dir DIR` | where throwaway workspaces are made, default `$TMPDIR/duet-eval-<uid>/work`. Refused if the results or the tasks could be reached within three `..` steps of a workspace. |
| `--keep-workspaces` | keep finished workspaces, moved to `<session>/workspaces/`, instead of deleting them. |

## Output

```
<results>/<session>/
  session.json                           one entry per invocation: order seed, limits, the plan,
                                         how many runs completed, why it stopped early (if it did)
  records/<task>__<arm>__s<seed>.json    one record per run
  records/superseded/                    earlier records for a run that was repeated in this session
  logs/<task>__<arm>__s<seed>/           prompt.md, command.json, stdout.txt, stderr.txt,
                                         diff.patch, visible.log, hidden.log,
                                         duet/ (the workspace's .duet folder), duet-run/ (a copy
                                         of the duet's run folder); one subfolder per member for
                                         best-of-two
  tasks/                                 the copy of the tasks this session read
  workspaces/                            kept workspaces (--keep-workspaces only)

$TMPDIR/duet-eval-<uid>/
  work/<opaque id>/ws                    one client's workspace, removed after scoring
  skill-<session>/                       the copy of the skill duet hosts are pointed at
```

A record holds: `visible` and `hidden` (pass/fail, exit code, test counts where pytest printed
them), `visible_pass_hidden_fail`, `seconds`, `status` (`ok`, `error`, `timeout`,
`not_started`), `exit_code`, `limits` (this run's own) and `session_limits`, `order` (seed and
position), and:

- `infra_failure`: why the run says nothing about the model, or `null`. Set when the client
  could not be started, when a duet arm held no duet (no run recorded for the workspace, or no
  peer turn), when a failed client's output names a permission, sign-in, quota or rate-limit
  error, or when a client failed before changing anything.
- `duet` (duet arms): `found` (`state-dir`, `workspace`, `final-message` or `null`), `ran`
  (the peer took a turn), `finished`, `per_protocol` (ran and finished), `peer_turns`. The run
  is looked up in `$DUET_HOME/runs` by the `workspace` field of its state, then in the
  workspace's own `.duet/`, then in the status report the host is asked to print.
- `tampered`: protected test files the arm changed, removed or added. All are undone before
  the visible command is scored. `runner_files_added`: `conftest.py` and test-runner
  configuration the arm added elsewhere; they never reach the oracle.
- `cost_usd` (`host_cost_usd`, `peer_cost_usd`): dollars a client reported, nothing else.
  `host_tokens`, `peer_tokens` and `token_basis`, which says what each count includes.
- `oracle_paths_seen`, `outside_paths_seen`: see "Leaks".
- `picked` and `members` (best-of-two): which member was kept, and each member's full result.

`summary` prints, per session, a results table (runs, infrastructure failures, timeouts, hidden
pass with a 95% Wilson interval, visible pass, looked-done-but-wasn't, duets finished, hidden
pass among finished duets) and a spend table. Every rate is shown as `hits/runs (percent)`.
Infrastructure failures are counted in their own column and left out of the rates. It warns
when arms did not run the same (task, seed) cells, when a session stopped early, and when runs
named the oracle. Sessions are never merged unless you pass `--merge`, which refuses sessions
run under different limits unless you add `--allow-mixed-limits`. No record is dropped in
favour of a later one: a repeat in the same session moves the earlier record to `superseded/`
and says how many there are, and merged sessions count every record.

## How scoring works

After the client's process has ended:

1. **Visible.** The workspace is copied, the task's `protected` files are restored from the
   starter and any file the arm added inside a protected folder is removed, then the visible
   command runs.
2. **Oracle.** A clean tree is built from the workspace's source only: no protected folder, no
   test files, no `conftest.py`, `pytest.ini`, `pyproject.toml`, `tox.ini`, `setup.cfg`,
   `sitecustomize.py` or `.pth` files. `hidden/` is added as `hidden_tests/` with an empty
   `pytest.ini` of the harness's own, and the oracle command runs there.
3. A command passes only if it exits 0 **and** its pytest summary reports no failure or error.
4. Both commands run with credential-like environment variables removed (any name containing
   `KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `CREDENTIAL` or `AUTH`).

Scoring still executes code a model wrote, outside any sandbox, as you. The environment is
scrubbed but your files are not out of reach. Run the harness on a machine where that is
acceptable.

## Leaks

What is done to keep an arm from seeing the oracle or another arm's work:

- Workspaces live under the temp folder; records, logs, diffs and hidden-test output live under
  the results root. Nothing of the harness is reachable within three `..` steps of a workspace,
  and `run` refuses a layout where it would be.
- Tasks are copied into the session folder and read from there. Duet hosts are pointed at a
  copy of the skill that has no `eval/` or `tests/`, recopied before every duet run. No prompt
  names the checkout.
- Arm order is shuffled per (task, seed), so if something does leak between runs it does not
  always favour the same arm.
- Claude clients are started with a permission rule that denies reading the results and the
  task source. The tests check that the rule is passed, not that a live client obeys it.

What is **not** prevented: Codex's sandbox can read any file by absolute path, a duet's Claude
peer is started by the duet runner and not by this harness, and the original task folders are
still on this disk. So every run is audited afterwards:

- `oracle_paths_seen` lists oracle names found in what the client printed, in its error output
  and in the duet transcript: the task folder, the results root and the task source by absolute
  path; `<task>/hidden`, `<task>/solutions`, `solutions/reference`, `solutions/naive`; and the
  hidden test files' own names. Treat a run with any entry as contaminated until you have read it.
- `outside_paths_seen` lists relative paths that climb out of the workspace (`../...`). This is
  weaker evidence: it shows the arm looked outside, not what it found.

The audit reads what was printed. A client that read a file without printing its path leaves no
trace. For a stricter setup, run on a machine or in a container where the task source does not
exist outside the session folder.

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

| Task | Kind | The trap |
|---|---|---|
| `cursor-feed` | multi-file repair | cursor ordering, compatibility, pagination and export must follow the integration contract |
| `dependency-planner` | multi-file repair | dependency closure and scheduling must agree between preview and submission |
| `slugify` | edge cases | accents, apostrophes, truncation that leaves a trailing hyphen, empty results |
| `duration` | input validation | a find-all regex accepts garbage, wrong order, repeats, decimals, non-ASCII digits |
| `split-bill` | subtle spec | who gets the leftover cents, ties, refunds, exactness on huge totals |
| `ttl-cache-bug` | debugging | the failing test points at one seeded defect; the class breaks its contract in several more places |
| `invoice-refactor` | refactor | a tidy rewrite drops rounding mode, discount fallback, sort order, returns; scored by differential test against the original |
| `layered-settings` | multi-file rollout | profile resolution, launch planning and operator reporting must preserve compatibility |
| `record-stream` | multi-file repair | transport chunk boundaries, final records and lifecycle handling must satisfy the wire contract |

Requirements are in `task.md` and, for the multi-file tasks, the supplied repository documentation,
release notes and compatibility tests. The visible tests do not exercise the whole contract.
`run_eval.py verify` proves, without any model, that for each task the starter fails, the
reference passes both suites, and the naive solution passes visible and fails hidden.

### Calibrating tasks

The historical five-task smoke set was too easy to separate the arms: each task was a small
single-file change with its traps spelled out. Four multi-file tasks have since been added.
Their presence does not establish useful difficulty; calibrate the current nine-task set on
solo arms before drawing conclusions about whether conversation helps.

A task earns its place when a single model passes its oracle roughly 30 to 70 percent of the
time. Below that it measures luck; above it there is no room for a second model to help. To
build such a set:

1. Write candidates closer to real work: changes that span several files, requirements spread
   across code, docs and tests, a bug whose first plausible fix is wrong, a judgement call the
   task text settles only if read closely. Keep every requirement derivable from what is in the
   workspace; an unstated preference is a guess, not a trap.
2. Run only the two solo arms on each candidate, three to five seeds, and keep the ones whose
   pooled solo pass rate falls between 30 and 70 percent. Measure time and reported cost on
   those candidates rather than assuming the old smoke runs predict either.
3. Check each kept task with `verify`, then run the full matrix on the kept set.
4. Do not tune tasks against duet results. Calibrate on the solo arms only, then freeze the set.

## Cost

Historical figures from a one-task smoke run at `--effort low`; these are observations of that
run, not estimates for the current task set or a future matrix:

| Arm | seconds | reported dollars |
|---|---|---|
| `solo-claude` | about 35 | about 0.40 |
| `solo-codex` | about 35 | none reported |
| `duet-claude-host` | about 250 | about 1.30 to 1.70 (host only; Codex peer unpriced) |
| `duet-codex-host` | about 150 to 250 | about 0.90 to 3.40 (Claude peer only; Codex host unpriced) |
| `best-of-two-solo` | about 70 | about 0.40 |

The current full matrix is 9 tasks × 3 repetitions × 5 arms = 135 runs, run sequentially.
No cost or duration estimate for that matrix follows from the smoke table. Measure a pilot
with the intended models, effort and current tasks, including unpriced Codex tokens, before
budgeting a larger comparison. More distinct calibrated tasks improve coverage of real work;
extra repetitions of the same tasks do not expand that coverage.

`--max-total-cost` counts reported dollars only, checks before each run, and so can be
overshot by one run and never sees Codex spend.

## Limits to keep in mind when reading results

- **Dollars are not comparable across labs.** Claude reports dollars, Codex reports tokens. A
  `duet-claude-host` cost omits the Codex peer; a `duet-codex-host` cost is the Claude peer
  only; `solo-codex` has none.
- **Token columns are not comparable either.** Host tokens count all input, cached included,
  plus output. Peer tokens are whatever the duet runner counted: with cached input for a Codex
  peer, without it for a Claude peer. They are shown apart and never summed.
- **"Same budget" means the same caps**, scaled per arm as the table above says, not the same
  spend. `best-of-two-solo` is the arm to compare a duet with when the question is whether the
  conversation, and not simply a second model, earns its cost.
- **A Claude peer ignores `--effort`.** In `duet-codex-host` the peer runs at its own default,
  so that arm is not at the effort you asked for on both sides.
- **A duet that did not happen is not a duet result.** Read the `infra` column and the
  finished-duet column before the hidden pass rate of a duet arm.
- **On a timeout the harness stops the client it started.** A duet peer runs in its own
  process group, started by the duet runner, and may outlive a host that was stopped.
- A Claude host's shell tool stops a command after ten minutes, so a slow peer turn can cut a
  duet short where an interactive session would not.
- A Codex host is given network access and a writable `~/.claude`, as the skill requires.

## Tests

```sh
python3 -m pytest tests/test_eval.py -q
```

Stand-in clients only (`tests/fake_eval_cli.py`); no model is called.

## Review micro-benchmark

A cheap test, separate from everything above, of the narrowest version of the two-model premise:
**does a fresh reviewer from the other lab catch more seeded defects than (a) the same model
reviewing in the session that already took the code in, or (b) a fresh session of the same
model?** If cross-lab review catches no more, and nothing different, than fresh same-lab review,
the premise is in trouble before a full comparison is paid for.

It lives in `eval/reviewbench/` and runs from `eval/run_reviewbench.py`. Nothing in it builds
software; reviewers only read one module and answer.

### What is run

22 small original Python modules (41 to 61 lines) in `eval/reviewbench/cases/`:

- **18 seeded cases**, each with exactly one planted defect that its happy-path tests do not
  notice: swallowed exception, off-by-one (two), missing input validation (two), non-atomic
  update, resource leak, unsafe default, path-check bypass, authorization bypass (two),
  degenerate input, unbounded read of huge input, timezone confusion, unit confusion, retry
  without bound, mutation of a shared default, partial failure.
- **4 clean controls** with no planted defect, to measure false alarms.

Each case holds `module.py` (what the reviewer sees), `case.json` (a plain description of what
the module is meant to do, and the hidden answer key: defect class, line range(s), one-sentence
statement, 2 to 5 keyword patterns), `test_hidden.py` and, for seeded cases, `fixed.py`.
`verify` proves without any model that, for every seeded case, the `test_happy_*` tests pass
and the `test_defect_*` tests fail on the module as shown, everything passes on `fixed.py`, and
the lines that differ between the two are the lines the key names; and that every control
passes its tests.

Two kinds of review are run for each case and each model (Claude, Codex):

| Cell | Calls | What happens |
|---|---|---|
| `self` | 2 | one session is shown the module and asked to describe it; the same session is then sent the review prompt |
| `fresh` | 1 | a new session is sent the review prompt |

The three conditions are views of those cells from the side of an "author" model X:
`self` = X's `self` cell, `fresh-same` = X's `fresh` cell, `fresh-cross` = the other model's
`fresh` cell. A fresh Codex review is therefore both Claude's `fresh-cross` and Codex's
`fresh-same`; it is one event and is paid for once.

The review prompt is byte-for-byte the same in every condition. It contains the description and
the line-numbered module, never the case's name, and says the module may have no defects. It
asks for `DEFECTS: NONE` or a numbered list of at most five items, each starting with its line
number(s) and one sentence.

### Scoring (by code, never by a model)

- **Strict catch:** one reported item both cites a line within 2 lines of a keyed range and
  matches one of the key's keyword patterns. A cited range longer than 20 lines points at
  nothing in particular and never counts as a line match. Only the first five items are scored.
- **Lenient catch:** some reported item does either. Both are always shown; when they give
  different verdicts the result depends on the scoring and is reported as inconclusive.
- **False alarm:** anything reported on a control.
- A reply with neither `DEFECTS: NONE` nor a list item is `unparsed`: it counts as a miss, is
  never a false alarm, and is counted in its own column.

Replies are stored and scored again at every `summary`, so changing a keyword or the tolerance
costs no calls.

### Decision rule (fixed before any run)

Comparing `fresh-cross` with `fresh-same` head to head only asks which model reviews better,
and gives the opposite answer for the other author. The rule therefore asks what a second look
adds to the author's own review. For each author X, over the seeded cases with all three
conditions present:

    net(X) = (defects missed by X's self review and caught by fresh-cross)
           - (defects missed by X's self review and caught by fresh-same)

| Result (strict scoring, both authors) | Reading |
|---|---|
| net ≥ +3 for both authors | **Supports** the premise, suggestively: worth paying for the fuller comparison. Not proof. |
| net ≤ 0 for both authors | **Against**: on this test a fresh session of the same model does as well as the other lab. |
| ≥ +3 for one author and ≤ −3 for the other | **One-sided**: one model is simply the better reviewer here. An argument for using that model, not for pairing labs. |
| anything else | **Inconclusive**. |

The same rule is applied to the lenient score; if the two disagree the verdict is inconclusive.
Fewer than 12 complete seeded cases for either author means no verdict at all.

With 18 cases only large differences mean anything. A 3-to-0 split has a two-sided sign-test
p of 0.25; it takes 6-to-0 to get under 0.05. So "supports" here means "not contradicted, and
big enough to justify the larger experiment", never "shown". The report prints the sign test
beside each net, lists the cases caught only by cross-lab and only by same-lab fresh review,
and, when run with `--samples 2`, how often the very same condition disagreed with itself,
which is the noise any difference has to clear.

### What it does not measure

- **It is not the pair-versus-solo comparison.** No software is built and no conversation takes
  place. A positive result says a second lab's reviewer sees different defects in small
  modules, not that a duet builds better software.
- **Self-review is approximated.** Paying a model to write each module would cost more and
  would not put the same defect in front of every reviewer, so the `self` session only reads
  and describes the module before reviewing it. Real authors have more invested in their own
  code than that; the approximation probably understates the blind spot it is meant to imitate.
- **Keyword scoring is crude.** A reviewer who describes the defect correctly in words no
  pattern anticipates is scored a miss under strict scoring; a vague remark that happens to use
  a keyword on the right line is scored a catch. The patterns were written by guessing how a
  reviewer would phrase things and have not been checked against real replies. Read the stored
  replies for the cases that decide the result before trusting a verdict.
- **The cases come from one side of the comparison.** They were drafted with a Claude model,
  so the planted defects are the ones that model thought of, in its style; this may make them
  easier or harder for Claude than for Codex, in a direction nobody has measured.
- **Seeded defects are not natural defects.** One defect per small tidy module, of a textbook
  kind, is easier to spot than a mistake buried in real work. Both models may catch nearly all
  of them, leaving no room for a difference; a pilot will show whether that ceiling is hit.
- **Extra findings on seeded cases are not judged.** A reviewer may report a real problem
  nobody planted, or noise; neither is scored. Only the four controls measure false alarms, and
  a model can also be "wrong" about a control only in the sense that nothing was planted there.
- **One sample per cell by default.** Model output varies from run to run.

### Safety and cost

- Reviewers get the module in the prompt and nothing else. Each call runs in a new empty folder
  under the temp folder. Claude is started with `--tools ""`, `--safe-mode`, a rule denying it
  the cases and the results, and `--max-budget-usd` per call. Codex is started in its
  `read-only` sandbox; it keeps a shell that can read the disk by absolute path and runs with
  your normal Codex configuration, so every call's output is searched for the names of the
  hidden files and the cases folder, and for shell commands, and `summary` warns if any appear.
  That search only sees what was printed.
- Hard caps, counted over every call the session has ever made: `--max-calls` (default: the
  size of the full plan) and `--max-total-cost` (default $20, Claude's reported dollars). A
  cell starts only if all of its calls fit under both, a Claude call being counted at the
  per-call cap (`--max-call-cost`, default $0.50). Codex reports no dollars, so it is bounded
  by the call cap and the per-call `--timeout` only. A run that meets a cap stops cleanly,
  records the cells left undone, and exits 1.
- Resumable: rerun the same command with the same `--session`. Finished cells are never
  repeated; a `self` cell whose first call succeeded resumes at its second. Cells that ended in
  an error are left alone unless `--retry-errors` is given. A failed call still counts toward
  the caps, since it may have been charged.
- Credentials are never written: the environment is not logged, and each client is started
  without the other lab's API key. Prompts hold only the module and its description.

**Calls and cost.** A full run is 22 cases × 2 models × 3 calls = **132 calls** (66 Claude,
66 Codex); `--dry-run` prints the exact number for any selection and session state. No live run
has been made, so there is no measured cost. Each call is one short prompt of roughly 1,000 to
1,500 tokens and a short reply, so a full run is expected to land within the $10 to $20 target,
but a client's fixed per-session overhead and the model and effort chosen decide that. Run the
two-case pilot below first and multiply.

### Commands

```sh
cd ~/.agents/skills/duet

python3 eval/run_reviewbench.py list                       # the cases
python3 eval/run_reviewbench.py verify                     # prove every case; no model calls

# exactly what would run, and how many calls; starts nothing
python3 eval/run_reviewbench.py run --cases all --dry-run
python3 eval/run_reviewbench.py run --cases all --dry-run --show-prompt

# pilot: one seeded case and one control, 12 calls, to measure cost per call
python3 eval/run_reviewbench.py run --cases s07-static-path,k02-intervals --session pilot --max-total-cost 3

# the full run: 132 calls, stops at $20 of reported Claude spend
python3 eval/run_reviewbench.py run --cases all --session full --max-total-cost 20

# continue a run that stopped at a cap or was interrupted (same command, same session)
python3 eval/run_reviewbench.py run --cases all --session full --max-total-cost 20

python3 eval/run_reviewbench.py summary                    # every session under ~/.duet-eval/reviewbench
python3 eval/run_reviewbench.py summary --results ~/.duet-eval/reviewbench/full
```

Other options: `--claude-model`, `--codex-model` and `--effort` (recorded; a session cannot be
resumed with different ones), `--samples N`, `--max-calls N`, `--max-call-cost D`,
`--timeout S`, `--order-seed N`, `--results DIR` (refused inside a git repository), `--work-dir DIR`.

Results go to `~/.duet-eval/reviewbench/<session>/`: `session.json` (each invocation, its
settings, why it stopped, what was left undone), `ledger.jsonl` (one line per call), `cells/`
(one record per cell, with the reviewer's reply), and `logs/` (prompt, command and raw output
of every call).

Tests (stand-in clients only, `tests/fake_review_cli.py`; no model is called):

```sh
python3 -m pytest tests/test_reviewbench.py -q
```
