# duet

A skill that puts the top Claude model and the top OpenAI model in **one conversation** to
frame, propose, plan, build and review until an agreed acceptance command passes and the model
that did not write the final change approves it.

Inspired by [karpathy/llm-council](https://github.com/karpathy/llm-council), with two changes:
two participants instead of a council, and they build the thing rather than only answer a question.

## How it works

```
 you ──goal──▶ host model (Claude Code session, or Codex)
                    │  say / peer / check / finish
                    ▼
              scripts/duet.py ──stdin──▶ peer model's CLI (one resumed session)
                    │
        ~/.local/state/duet/runs/<run-id>/     (outside the workspace)
          conversation.md   readable transcript of everything said
          messages.jsonl    the same, as the source of truth
          state.json        current phase, session id, turn count, last check, caps
          turns/NNN/        exact prompt and raw output of each peer turn
          drafts/           where the host writes its message files
          checks/NNN.log    full output of each acceptance run
```

- **The host** is the model you are talking to. It takes part directly.
- **The peer** is the other lab's model, run through its own command-line client: `codex exec`
  for a Claude host, `claude -p` for a Codex host. The first turn opens a session; every later
  turn resumes that same session, so the peer remembers the whole conversation and is sent only
  what is new: the host's messages, anything you said, and acceptance results.
- **The run store** is outside the workspace, in `$DUET_HOME/runs/<run-id>` (`DUET_HOME`
  defaults to `~/.local/state/duet`; folders are created with mode 0700). The peer works in the
  workspace; an external store reduces accidental discovery but does not guarantee isolation.
  A Claude peer inside Codex inherits the host's `--add-dir` permissions, which can include it. If
  that directory cannot be written, as inside a Codex sandbox started without
  `--add-dir ~/.local/state/duet`, `init` says so in one line and stores the run in
  `<workspace>/.duet/<run-id>` instead. Both locations use the integrity record described below.
- **Models** are not hardcoded. The host is whatever model you are running; the peer uses its
  client's configured default unless you pass `--peer-model`. Defaults do not guarantee the
  latest or strongest model; select the intended models in each client and report them honestly.

### The loop

A run is a sequence the runner enforces: `frame` (optional) → `propose` → `plan` → `build` ↔
`review` → done. The current phase is kept in `state.json`; a `say` or `peer` that names a phase
which cannot follow it is refused with exit 6 and a message listing what is allowed.

| Phase | What happens | Enforced by the runner |
|---|---|---|
| frame | Map requirements to observable checks, run the baseline, and agree what distinguishes success; identify a plausible wrong implementation that would pass the command | Only at the start. A run needs an acceptance command or an explicit waiver before it can leave this phase (`--accept-later` starts without one); the command then locks and changing it later needs your agreement |
| propose | Inspect repository constraints; each model gives a design, three independent input/expected-output cases derived from the requirements, and its riskiest assumption | Blind: the peer cannot be asked until the host's proposal is recorded; the host's proposal is withheld from the peer and stored encoded on disk until the peer has proposed, and the host cannot propose again after that |
| plan | Identify consequential differences and deciding evidence; combine compatible parts without inventing disagreement. Agree on ordered tasks with a driver and reviewer per task; by default the non-implementer writes tests from the proposal cases | Refused until both proposals exist |
| build | One model edits at a time; the host names the one task before a peer build turn | Refused until each model's latest plan message ends in an approve verdict, or you have settled the disagreement. Write access is allowed only in build turns; a read-only change voids approval and is restored only when a usable snapshot permits it |
| review | Inspect the current files, attempt the highest-risk uncovered requirement, and separate observed results from reasoning. Keep accepted counterexamples as regression tests in a later build turn | Follows build only. Only review-phase verdicts count toward finish; approving your own change is neither needed nor counted |

Once an acceptance command is set, run `check` for the baseline **during frame, before acceptance
locks**. If acceptance is waived, agree on evidence that will establish success instead. A refactor or other
behaviour-preserving task may legitimately start green; separately check the requested structural
or performance change. State what distinguishes success from the baseline. For other tasks, an
unexpectedly green baseline is a reason to examine whether the command covers the goal. Fix the
command in frame if needed; later changes need `accept --by-user "<words>"`.

Verdicts have phase-specific meanings: in plan, `APPROVE` means **ready to implement**. In review,
approve only after inspecting the current files, seeing acceptance pass on those files (unless
explicitly waived), and testing an uncovered case. If a sandbox blocks that case, give the exact
command for the other model to run as an experiment and wait for the result before approving.

The default test/implementation split is per piece of work: the model that does not implement it
writes its tests, in a build turn, from the independent proposal cases (or requirements when
proposals were skipped). Explain exceptions and changed expectations. Review must list changed
test/check files, identify removed or loosened assertions, and note when a planned split did not
happen. These are participant duties; the runner does not verify test authorship or the quality
of review evidence. If a named build task or approved plan is ambiguous, ask before editing;
otherwise report the task, changed files, checks and results, and remaining gaps.

The rest of the rules, each one line:

- build and review alternate freely; that is the iterate loop.
- Nothing goes back to frame or propose. Going back to plan from build or review needs
  `--replan "<reason>"`, which is recorded; build reopens once both have approved a plan again,
  and `finish` is refused while the run sits in plan. Replanning requires fresh reviews too.
- `check` runs the acceptance command and `experiment` runs an agreed command; both work in any
  phase, cost no peer turn and do not move the run. (There is no separate test phase.)
- You can speak in any phase (`say --speaker user`); your words never move the run.
- A failed or timed-out peer turn does not move the run. Its record says whether workspace files
  changed during it, because a turn can finish its work and still fail to report back.
- `--skip-proposals "<reason>"` at `init` lets a run go from frame straight to plan or build.
- `--phase debate` is still accepted and means `plan`; `--phase test` is refused with a pointer
  to `check`. Runs started before phases were tracked load as before: their phase is read from
  the last message either model recorded.

`finish` succeeds only from build or review, when the acceptance command last passed on the current
files (unless waived) and those same files (compared by content fingerprint) were approved in
review by everyone who must. The runner keeps the set of models that have changed files since
they were last fully approved, by comparing the workspace around every command: one writer needs
the other model's approval; two writers, or none, need both. A host edit made just before a peer
build turn is recorded as the host's before the peer starts, and an edit no command saw is put
down to the host. Any later edit reopens the gate. Acceptance changes and replanning invalidate
old reviews; a peer approval cannot cover a check recorded after its prompt was prepared. Pending
checks and experiments block finish. A finished run refuses operational changes; `reseal` is an
explicit administrative exception and does not reopen it.

`experiment --cmd "..."` runs a command the two models agreed would settle a disagreement and
puts its output into the conversation.

### The engineering standard

Both models are told, in every run and again on each build and review turn, to work as the CTO and
the head of engineering of a top software company would with their own names on the result, and
to build the long way: the strongest route rather than the quickest, nothing left as a stub or placeholder, failure
paths handled as carefully as the happy path, causes fixed rather than masked, safe defaults,
deliberate compatibility and migrations, tests that can fail, and no complexity the goal does
not need. Claims must be verified. Scope and hard turn, cost and time caps still apply: stop
incomplete when needed. An explicit draft or prototype narrows scope without hiding defects.
Each phase applies it: the frame agrees which
pressures the result must survive, proposals list the failure modes they handle, the plan makes
error handling and its tests explicit tasks, and the reviewer is told to try to break the work
and to refuse anything that only works on the happy path. The full text is `STANDARD` in
`scripts/duetlib/prompts.py` and the matching section of `SKILL.md`.

This is an instruction to both models, backed by the review gate (the other model must approve
the final files); the runner does not itself measure code quality.

### Stop conditions

- **Turn cap**: 30 peer turns by default (`--max-turns`), failed and interrupted turns included;
  raising it needs your say-so (`extend --turns`).
- **Cost cap**: `--max-cost <dollars>` stops peer turns once the peer's client has reported that
  much; `extend --cost <dollars>` raises it. The Claude client reports cost; Codex reports tokens
  only, shown in `status`.
- **Stall**: three acceptance failures in a row with the same output (ignoring numbers, memory
  addresses and temp paths) block further peer turns until you weigh in.
- **Peer failure or timeout**: recorded in the conversation; the peer's whole process group is
  stopped; the session is kept and resumed, with a note that messages may repeat.
  `peer --new-session` replays the conversation into a fresh session if the old one cannot resume;
  if that turn fails too, the next turn still replays.
- **A runner that is stopped mid-turn**: the pending turn is written to `state.json` before the
  peer starts. On SIGTERM, SIGINT or SIGHUP the runner stops the peer's process group, attempts
  read-only recovery if a usable snapshot exists, counts the turn and records it. If the runner
  is killed outright, the next command does the same from the record (the peer process may then
  still be running: stop it).
  Changes left by such a turn are put down to the peer.
- **One peer turn at a time**: a second `peer` on the same run is refused at once (exit 2).
- **One check or experiment at a time**: its pending record blocks `finish` until the result is
  saved. After interruption, the next mutating command records the failure and invalidates stale
  evidence. The shell's process group is stopped on return, including background children.

### Safeguards

- Message text reaches the peer on standard input only, never in a command line.
- Each message is wrapped in a tag carrying a per-run random marker, so text inside a message
  (command output, quoted material) cannot pass itself off as another speaker.
- The peer's process does not inherit environment variables whose names end in `_TOKEN`,
  `_SECRET`, `_API_KEY` or `_PASSWORD`, unless they carry its own client's prefix (`OPENAI_` or
  `CODEX_` for Codex, `ANTHROPIC_` or `CLAUDE_` for Claude) or are AWS credentials with
  `CLAUDE_CODE_USE_BEDROCK` set. `DUET_KEEP_ENV=NAME,NAME` keeps ones a build needs.
- Run state is written under a file lock, and every command re-reads it under that lock and
  changes only its own fields, so a long `check` cannot overwrite a peer turn saved meanwhile.
  `.duet/` in the workspace is never part of the
  fingerprint, and when it exists (an in-workspace run, or a goal file kept there) it is added to
  the repository's local git exclude list so transcripts are not committed by accident.
- **Read-only changes void the turn's verdict; restoration depends on a usable snapshot.**
  For a Claude peer without `--write`, the runner copies files the fingerprint covers into the
  turn folder (copy-on-write clones on APFS, plain copies elsewhere). Codex relies on its sandbox
  and skips the snapshot unless `DUET_SNAPSHOT=always`. With a usable snapshot, restoration
  reverts edits, removes created files, returns deleted files and keeps modes. Ignored files and the run
  store are never touched. The changed versions are saved in `turns/<n>/readonly-violation/`
  (`changes.txt`, `files/`, `changes.patch`), the conversation records it, any approval from
  that turn is dropped and `peer` exits 8. The runner cannot tell the peer's change from an edit
  you made during the turn, and says so; a changed file that could be neither copied nor moved
  into that folder is left in place rather than overwritten. A restore never writes through a
  symlinked folder, and if the workspace still differs afterwards the copy stays in
  `turns/<n>/snapshot-kept`. Above `DUET_SNAPSHOT_MAX_MB` (default 1024) of source
  files no copy is taken: a warning says so and a change is reported but left in place.
- **Integrity record for every run.** After each write the runner stores the SHA-256 of
  `state.json` and `messages.jsonl` outside the workspace (in `DUET_GUARD_DIR` if set, else
  `~/.local/state/duet-guard`, else the temp directory), and every command checks them first, as
  does the runner straight after each peer turn, before it writes anything. A hand-edit of an approval, of
  `last_check` or of anything else in those two files, or a deleted record, stops every command
  with exit 9 until you run `reseal --by-user "..."`. Older runs without a record also need explicit
  resealing. This detects tampering; it does not prevent it. A peer that reads the runner's source
  and rewrites the record as well is not caught, and `conversation.md`, `turns/` and `checks/`
  are not covered. An external state directory can be writable through inherited host permissions.
- Every acceptance run's full output is saved under `checks/`; the conversation gets the start
  and the end. A check that itself changes files names them and cannot close the run.
- A peer session that failed before completing any turn is discarded, so the rules and the goal
  are always delivered.

## Both directions

| Host | Peer | Start with | Peer's read-only turns | Peer's build turns |
|---|---|---|---|---|
| Claude Code | Codex | `init ...` | Codex sandbox `read-only` | Codex sandbox `workspace-write` |
| Codex, sandboxed | Claude | `init --peer claude ...` | Claude plan mode inside Codex's sandbox | Claude edit mode |
| Anything not sandboxed | Claude | `init --peer claude ...` | Claude's sandbox denies writes to the workspace; edit tools off | Claude edit mode in its sandbox |

Read-only turns of a Claude peer also have a snapshot-and-restore backstop: changes are saved
and restored when the snapshot and recovery succeed. A Codex peer's read-only turns rely on
its own sandbox, which blocks writes at the operating-system level, so no snapshot is taken (set
`DUET_SNAPSHOT=always` to force one); the runner still compares the files before and after and
reports any change, but does not undo it. Codex's sandbox does not confine MCP servers set up in
your Codex configuration, so a write made through one would be reported, not reverted.

### Sandbox recovery

Both have been run live end to end. Notes for **Codex as host**:

- The Claude client needs the network and its own folder, which Codex's default sandbox blocks.
  Start Codex with `-c 'sandbox_workspace_write.network_access=true' --add-dir ~/.claude
  --add-dir ~/.local/state/duet`, or approve the `peer` command to run outside the sandbox. The
  runner refuses when it detects a Codex sandbox without network. Without the state-directory
  `--add-dir` the run is stored in the workspace (easier accidental discovery, see above).
  If `init` or `peer` reports this restriction, stop and relay the message. The user must restart
  Codex with those permissions or approve the external command; do not try other ways around
  the sandbox. `init` checks client installation and known sandbox restrictions, not sign-in;
  an unsigned-in client fails on its first peer turn.
- Claude joins as a clean session (`--safe-mode`): no personal hooks, instructions or plugins.
  `--peer-user-config` loads them instead; measured on a one-line reply, that cost about six
  times as much.
- When the host is not sandboxed, Claude runs commands in its own sandbox with no network and
  no way to run a command outside it. On read-only turns that sandbox denies writes to the
  workspace (`sandbox.filesystem.denyWrite`) and the edit tools are switched off; plan mode is
  not used. It also cannot read the run store (`denyRead` plus `Read` deny rules).
- When the host itself is sandboxed, Claude's own command sandbox cannot start inside it, so the
  host's sandbox does the confining. That sandbox allows writes to the workspace, so read-only
  turns use plan mode (edit tools blocked, shell commands not) and rely on the restore backstop.

### Read-only recovery

After a read-only violation, inspect `turns/<n>/readonly-violation/`: `changes.txt`, `files/`, and
`changes.patch` preserve the change. A host or user edit made during the turn is indistinguishable
from a peer edit and is saved there too; never edit concurrently with a peer turn. The verdict is
void. If the change was only test by-products, add those by-products to `.gitignore`, run `check`,
and request review again. Otherwise resolve any remaining changes and request another review.

Do not assume the workspace was restored. Above `DUET_SNAPSHOT_MAX_MB` (default 1024 MB), or if
copying the snapshot fails, there may be no snapshot to restore. Saving a changed file or restoring
the snapshot can also fail; the runner preserves evidence where possible and reports what remains.
Read those warnings and inspect the files before continuing. A failed peer turn can return exit 5
even when its read-only violation was recorded; the same recovery duties apply.

## Use

In Claude Code or Codex, ask for it: "run a duet with Codex to build X", or `/duet`.

By hand:

```sh
D="python3 ~/.agents/skills/duet/scripts/duet.py"
$D init --workspace . --goal-file /tmp/goal.md --accept "python3 -m pytest -q"   # prints the run folder
# the goal can also come on stdin; write message files into <run>/drafts/
$D check --run <run>                          # baseline in frame; record what success must change
# refine acceptance with `accept --cmd` here if needed, before leaving frame
$D say  --run <run> --phase propose --file <run>/drafts/proposal.md
$D peer --run <run> --phase propose            # peer's blind proposal
$D say  --run <run> --phase plan --file <run>/drafts/reply.md --verdict revise
$D peer --run <run> --phase plan               # compare evidence, combine compatible parts, assign tasks
$D say  --run <run> --phase plan --file <run>/drafts/agreed.md --verdict approve   # build needs both to approve
$D experiment --run <run> --cmd "python3 probe.py"   # settle a dispute by evidence
$D say  --run <run> --phase build --file <run>/drafts/task.md   # name the one task
$D peer --run <run> --phase build --write      # peer drives that task
$D check  --run <run>                          # run the acceptance command
$D say  --run <run> --phase review --file <run>/drafts/review.md --verdict approve
$D finish --run <run>                          # the peer wrote, the host approved: done
$D status --run <run>                          # phase, what may follow, what is blocking (JSON)
```

If the plan becomes invalid **before finishing**, return to plan explicitly and get fresh
approvals before resuming build:

```sh
$D say  --run <run> --phase plan --replan "the API has no prefix scan" --file <run>/drafts/replan.md
```

Other options: `--peer claude` (when the host is Codex), `--peer-model`, `--peer-effort`
(Codex reasoning effort for the run), `--peer-user-config` (Claude peer), `--max-cost`,
`--skip-proposals "<reason>"`, `peer --new-session`, `--waive-accept "<reason>"`, `--accept-later`
(the frame phase must then set the command), `accept --cmd "<command>"` or `accept --waive
"<reason>"` to set or replace the acceptance (with `--by-user "<words>"` after frame),
`extend --turns N --cost DOLLARS --reason "..."`, `reseal --by-user "<words>"`.

Defaults: peer `codex`, 30 peer turns, no cost cap unless `--max-cost` is supplied. Timeouts are
2400 seconds for `peer`, 1800 for `check`, and 600 for `experiment` (override with `--timeout`).
`extend --turns` and `--cost` add to existing caps; `--cost` cannot create a cap that was not set
at init. Extensions need the user's instruction, recorded in `--reason`.

### Exit codes

| Code | Meaning and response |
|---|---|
| 0 | Command succeeded. Only successful `finish` means the run is complete; use `status` to inspect it. |
| 1 | Acceptance check or experiment failed (including timeout). Read its recorded output and address the failure. |
| 2 | Usage error, invalid run folder, unavailable client or known sandbox restriction, concurrent peer turn, or attempted mutation of a finished run. Correct the input, report launch restrictions, wait for an active turn, or leave a finished run unchanged as appropriate. |
| 3 | Turn or cost cap reached. Report progress; extend only with the user's instruction using `extend --turns N` or `--cost DOLLARS`, plus `--reason "<their words>"`. |
| 4 | Three consecutive acceptance failures with the same normalized output stalled the run. Ask the user and record their answer with `say --speaker user` before continuing peer turns. |
| 5 | Peer failed, timed out or was interrupted. Inspect its recorded output and changed-file report; follow the retry procedure below. |
| 6 | Phase out of order, including blind-proposal and plan-approval gates. Read the allowed phases in the error or `status`; use `--replan "<reason>"` for a justified return to plan. |
| 7 | Finish refused. Resolve the blockers listed by `status`, then retry `finish`. |
| 8 | A read-only peer turn changed workspace files. Its verdict is void; inspect saved evidence and restoration warnings, then follow [read-only recovery](#read-only-recovery). Files are not guaranteed to have been restored. |
| 9 | A run's `state.json` or `messages.jsonl` differs from its integrity record, or that record is missing. Approvals/check results are untrusted. Stop; only after the user inspects and accepts the record, run `reseal --by-user "<their words>"`. |
| 70 | Internal runner fault. Report it; do not interpret it as a failed acceptance check. |

For exit 5, if workspace files changed, the work may already be complete on disk: read the files
and run `check` before deciding what to do next. Otherwise retry once in the same session. If it
fails again because that session cannot resume, retry once with `peer --new-session`, which
replays the conversation into a fresh session. On any other repeated failure, or a failed fresh
session, stop and tell the user. Never continue alone and call it a duet.

## Install

The canonical copy lives at `~/.agents/skills/duet`, linked into both clients:

```sh
git clone <this repo> ~/.agents/skills/duet
ln -s ~/.agents/skills/duet ~/.claude/skills/duet
ln -s ~/.agents/skills/duet ~/.codex/skills/duet
```

Needs Python 3.9+ (standard library only) and the `codex` and `claude` clients signed in.

## Things to know

- Everything in the conversation, and any file the peer reads in the workspace, is sent to the
  other lab's model. Keep secrets out of the goal and the workspace.
- Peer turns use your Codex or Claude plan at that client's configured model and effort. A long
  run at maximum effort is expensive; `--peer-effort` and `--max-turns` are the controls.
- The restore after a read-only turn covers the files the fingerprint covers. A peer that
  writes an ignored file, a cache, or something outside the workspace that its sandbox allows
  (the temp directory) is not undone.
- Both sandboxes let the peer read most of the disk. Only a Claude peer running its own sandbox
  is denied reading the run store; a Codex peer is merely told not to look, and the store is no
  longer somewhere it would search.
- Claude's sandbox leaves an empty `.claude/` folder in the workspace; the runner removes it
  after the turn if it was not there before.
- The fingerprint covers files git tracks or would track when the workspace is itself the top of
  a repository; any other workspace is walked. Ignored files are not part of it, and tool caches
  are skipped by name (`__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.tox`,
  `node_modules`, `.venv`, `*.pyc`); folders such as `build` or `dist` count unless git ignores
  them. Nested repositories are compared file by file; empty folders, permission bits and
  `.git/info/exclude` are covered. Every regular file is hashed by its complete contents using
  bounded-memory streaming; symbolic links are compared by their target.
- A peer verdict counts only as the last non-empty line of its reply, outside any code fence;
  the host records its verdict explicitly with `say --verdict approve|revise`.
- `init` refuses a `DUET_HOME` inside the workspace.
- Symlinked stores cannot redirect runs into the workspace, and `DUET_GUARD_DIR` must be outside
  both the workspace and state directory. Empty commands/reasons and nonpositive timeouts are refused.
- The blind round stops accidental anchoring, not a determined reader: the sealed proposal is
  encoded, not encrypted, in both store locations.
- There is no evidence yet that the pair beats one model working alone at the same budget.
  A comparison harness exists in `eval/` (see `eval/README.md`): it runs the same task through
  solo, duet and best-of-two-solo arms and scores each result against a hidden oracle. It can
  show how often work that looked done was wrong, with an interval beside every rate; on the
  historical five-task smoke set every arm passed. The current suite has nine tasks; those old
  results do not establish its difficulty or expected outcomes. Pricing is not comparable across
  labs (Claude reports dollars, Codex tokens).

## Tests

```sh
python3 -m pytest tests -q
```

The suite uses local stand-ins for both clients, never calls a model, and keeps its runs in a
temporary `DUET_HOME`.
