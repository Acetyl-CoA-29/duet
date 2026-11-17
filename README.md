# duet

duet is a skill for Claude Code and Codex. It puts a Claude model and an OpenAI model into the
same conversation and has them build something together: they agree what "done" means, each
writes a design without seeing the other's, they settle on one plan, and then they take turns
building and reviewing. A run ends when an acceptance command passes on the final files and the
model that did not write the last change has approved it.

One of the two models is the **host**: the one you are already talking to. The other is the
**peer**, reached through its own command-line client. A small Python runner sits between them.
It carries messages, keeps the record, and refuses to let either model skip a step.

## Install

```sh
git clone https://github.com/Acetyl-CoA-29/duet ~/.agents/skills/duet
ln -s ~/.agents/skills/duet ~/.claude/skills/duet
ln -s ~/.agents/skills/duet ~/.codex/skills/duet
```

You need Python 3.9 or later (standard library only) and both the `claude` and `codex` clients
installed and signed in.

Then, in Claude Code or Codex, ask for it: "run a duet with Codex to build X", or `/duet`.

## What a run looks like

A run moves through five phases in order. The runner tracks the phase and refuses a step that
does not belong (exit 6, with a message listing what is allowed).

| Phase | What the models do | What the runner enforces |
|---|---|---|
| frame | Agree what success means, map each requirement to something observable, and settle the acceptance command | The run cannot leave this phase until there is an acceptance command that has been seen to fail, or an explicit waiver. After frame the command is locked |
| propose | Each writes a design, three input/expected-output cases, and its riskiest assumption | The round is blind. The peer is not asked until the host's proposal is recorded, and the host's proposal is withheld until the peer has answered |
| plan | Compare the two designs, name the differences that matter and what evidence would decide them, and agree on ordered tasks with a driver and a reviewer for each | Needs both proposals. Build stays shut until each model's latest plan message approves |
| build | One model edits at a time. By default, whoever is not implementing a piece writes its tests | Write access exists only in build turns |
| review | Read the current files, try to break them, and report what was actually run | Only review verdicts count toward finishing, and nobody's approval of their own change counts |

Build and review alternate as often as needed. Nothing goes back to frame or propose. Going back
to plan takes `--replan "<reason>"`, and both models must approve a plan again before build
reopens.

`--skip-proposals "<reason>"` at `init` skips the blind round for small, well-specified jobs.

## How the pieces fit

```
 you ──goal──▶ host model (your Claude Code or Codex session)
                    │  say / peer / check / finish
                    ▼
              scripts/duet.py ──stdin──▶ peer model's CLI (one resumed session)
                    │
        ~/.local/state/duet/runs/<run-id>/
          conversation.md   readable transcript
          messages.jsonl    the same, as the source of truth
          state.json        phase, session, turn count, last check, caps
          turns/NNN/        exact prompt and raw output of each peer turn
          drafts/           where the host writes its message files
          checks/NNN.log    full output of each acceptance run
```

The peer runs as `codex exec` when Claude hosts and `claude -p` when Codex hosts. Its first turn
opens a session and every later turn resumes it, so the peer remembers the conversation and is
sent only what is new: the host's messages, anything you said, and check results.

The run is stored outside the workspace, in `$DUET_HOME/runs/<run-id>` (`DUET_HOME` defaults to
`~/.local/state/duet`). If that directory cannot be written, as inside a Codex sandbox started
without `--add-dir ~/.local/state/duet`, `init` says so and stores the run in
`<workspace>/.duet/<run-id>` instead.

Model names are not hardcoded. The host is whatever you are running, and the peer uses its
client's configured default unless you pass `--peer-model`. `status` and the finish message name
both models and say how each is known. The Claude client reports the model it ran. The Codex
client does not, so a Codex model is shown as requested, as the configured default when the run
started, or as not known. The host states its own with `--host-model`.

## What it takes to finish

`finish` succeeds only from build or review, and only when all of this holds:

- The acceptance command last passed on the files as they are now, compared by content. Any
  later edit reopens the gate. (A run with a waiver skips this.)
- Those same files were approved in review by everyone who has to. The runner tracks which
  models have changed files since the last full approval: one writer needs the other model's
  approval, and two writers, or none, need both.
- Each approval came after the evidence it relies on. An approval does not cover a check the
  reviewer was never sent, and it is voided by a replan, a change to the acceptance command, a
  justification, or anything you say afterwards.
- No check or experiment is still running, and no quality flag is open.

### The engineering standard

Both models are told to work as the CTO and head of engineering of a good software company would
with their names on the result: take the sturdier route over the quicker one, leave no stubs,
handle failure paths as carefully as the happy path, fix causes instead of hiding them, write
tests that can fail, and add nothing the goal does not need. Turn, cost and time caps still
apply, and an explicitly requested prototype narrows the scope without excusing defects. The
full text is `STANDARD` in `scripts/duetlib/prompts.py`, with a matching section in `SKILL.md`.

Most of that is an instruction, backed only by the other model's review. The runner checks two
parts of it itself.

**Quality flags.** At `init` the runner records, for each file, counts of unfinished-work
markers (TODO, FIXME, not implemented), functions with no body, errors caught and silently
dropped, tests, assertions, skipped tests, and strings shaped like credentials. When files
change it counts again and raises a flag wherever a number got worse or a test disappeared.
Flags are listed under `quality_flags` in `status`, shown to the peer on every review turn, and
block `finish`. You clear one by fixing the code, or by recording why the code is right as it
stands with `justify`, which sends both models back to review. Python is parsed properly; other
languages are matched by pattern and will raise some false alarms. A run with no flags has not
been shown to be good code. It has only been shown not to contain these particular problems.

**Acceptance that can fail.** A command that cannot fail proves nothing when it passes, so the
run cannot leave frame, or finish, until the acceptance command has failed once. A failing
`check` while the goal is still unmet counts. For work that starts green, such as a refactor,
`probe --break "<command>"` copies the workspace to a scratch folder, breaks the copy, and
requires the acceptance command to fail there. You can waive the requirement in your own words
with `probe --by-user` or `--accept-unproven` at `init`. An acceptance command that uses
absolute paths into the real workspace cannot be probed this way.

Runs started before version 1.10 carry neither check.

### Your part in a run

You can speak at any point with `say --speaker user`. Your words never move the run on their
own. Two things are deliberate:

- Only a message recorded with `--settle` ends a disagreement about the plan and opens build.
- Anything you say after a review makes that review stale. Both models review again, and the
  peer must have been sent your words first.

Changing the acceptance command after frame, raising a cap, and resealing a run's record all
need your agreement, recorded in your own words.

### When a run stops early

- **Turn cap.** 30 peer turns by default (`--max-turns`), counting failed and interrupted ones.
  `extend --turns N` raises it.
- **Cost cap.** `--max-cost <dollars>` stops peer turns once the peer's client has reported that
  much. The Claude client reports dollars. Codex reports tokens only.
- **Stall.** Three acceptance failures in a row with the same output block further peer turns
  until you weigh in.
- **Peer failure or timeout.** The peer's process group is stopped and the failure is recorded.
  The session is kept and resumed. `peer --new-session` replays the conversation into a fresh
  session if the old one cannot resume.
- **Runner interrupted.** The pending turn is written down before the peer starts. On SIGTERM,
  SIGINT or SIGHUP the runner stops the peer and records the turn. If the runner is killed
  outright, the next command records the turn from what was written down, and the peer process
  may still be running: stop it yourself.

Only one peer turn, and one check or experiment, can run at a time.

## Commands

```sh
D="python3 ~/.agents/skills/duet/scripts/duet.py"

$D init --workspace . --goal-file /tmp/goal.md --accept "python3 -m pytest -q"   # prints the run folder
$D check --run <run>                           # baseline: it should fail while the goal is unmet
$D say  --run <run> --phase propose --file <run>/drafts/proposal.md
$D peer --run <run> --phase propose            # the peer's blind proposal
$D say  --run <run> --phase plan --file <run>/drafts/reply.md --verdict revise
$D peer --run <run> --phase plan
$D say  --run <run> --phase plan --file <run>/drafts/agreed.md --verdict approve
$D say  --run <run> --phase build --file <run>/drafts/task.md   # name one task
$D peer --run <run> --phase build --write      # the peer does that task
$D check  --run <run>
$D say  --run <run> --phase review --file <run>/drafts/review.md --verdict approve
$D finish --run <run>
$D status --run <run>                          # phase, what may follow, what is blocking (JSON)
```

Other commands:

```sh
$D experiment --run <run> --cmd "python3 probe.py"       # run something both agreed would settle a dispute
$D probe --run <run> --break "rm src/feature.py"         # show acceptance fails when the behaviour is broken
$D justify --run <run> --flag "empty:src/hooks.py" --reason "an extension point"
$D say --run <run> --phase plan --replan "the API has no prefix scan" --file <run>/drafts/replan.md
$D accept --run <run> --cmd "<command>" --by-user "<your words>"   # replace acceptance after frame
$D extend --run <run> --turns 10 --reason "<your words>"
```

Write message files into `<run>/drafts/`, not the workspace: the finish gate compares workspace
files, and a stray message file there counts as a change.

Options at `init`: `--peer claude` (when Codex hosts), `--peer-model`, `--host-model`,
`--peer-effort` (Codex reasoning effort), `--peer-user-config` (load your own Claude settings
into a Claude peer), `--max-turns`, `--max-cost`, `--skip-proposals "<reason>"`,
`--waive-accept "<reason>"`, `--accept-later`, `--accept-unproven "<your words>"`.

Defaults: the peer is Codex, the cap is 30 peer turns, and there is no cost cap unless you set
one. Timeouts are 2400 seconds for `peer`, 1800 for `check` and 600 for `experiment` and
`probe`; `--timeout` overrides them.

### Exit codes

| Code | Meaning and what to do |
|---|---|
| 0 | The command succeeded. Only a successful `finish` means the run is complete. |
| 1 | A check, experiment or probe failed or timed out. Read its output and fix the cause. |
| 2 | Usage error, bad run folder, missing client, known sandbox restriction, a second peer turn while one is running, or a change to a finished run. |
| 3 | Turn or cost cap reached. Report progress. Extend only on the user's instruction. |
| 4 | The run stalled on three identical acceptance failures. Ask the user and record the answer with `say --speaker user`. |
| 5 | The peer failed, timed out or was interrupted. See below. |
| 6 | Phase out of order, including the blind-proposal, plan-approval and acceptance-proof gates. The error and `status` list what is allowed. |
| 7 | Finish refused. `status` lists what is blocking. |
| 8 | A read-only peer turn changed files. Its verdict is void. Follow [read-only recovery](#read-only-recovery). |
| 9 | The run's record does not match its integrity record, or that record is missing. Stop. After the user has inspected the run, `reseal --by-user "<their words>"`. |
| 70 | A fault in the runner itself. Report it. It is not a failed check. |

After exit 5, look at whether files changed during the turn. A turn can finish its work and
still fail to report back, so read the files and run `check` before deciding anything. If
nothing changed, retry once in the same session. If that fails because the session cannot
resume, retry once with `peer --new-session`. After any further failure, stop and tell the user.
Do not carry on alone and call the result a duet.

## Either model can host

| Host | Peer | Start with | Peer's read-only turns | Peer's build turns |
|---|---|---|---|---|
| Claude Code | Codex | `init ...` | Codex sandbox, `read-only` | Codex sandbox, `workspace-write` |
| Codex, sandboxed | Claude | `init --peer claude ...` | Claude plan mode inside Codex's sandbox | Claude edit mode |
| Anything not sandboxed | Claude | `init --peer claude ...` | Claude's own sandbox denies writes to the workspace | Claude edit mode in its sandbox |

Both directions were run end to end on earlier versions. The checks added in 1.10 have so far
been exercised only by the test suite.

### Sandbox recovery

When Codex hosts, the Claude client needs the network and its own folder, and Codex's default
sandbox blocks both. Start Codex with:

```sh
codex -c 'sandbox_workspace_write.network_access=true' --add-dir ~/.claude --add-dir ~/.local/state/duet
```

or approve the `peer` command to run outside the sandbox. The runner refuses to start a turn
when it detects a Codex sandbox without network. If `init` or `peer` reports that restriction,
stop and pass the message on. The fix is to restart Codex with those permissions or approve the
command, not to find another way around the sandbox.

`init` checks that the client is installed and that no known sandbox restriction applies. It
does not check sign-in, so a client that is not signed in fails on its first turn.

A Claude peer joins as a clean session with none of your hooks, instructions or plugins.
`--peer-user-config` loads them, at a cost: on a one-line reply it was about six times as
expensive.

How a Claude peer is confined depends on the host. If the host is not sandboxed, Claude runs
commands in its own sandbox with no network; on read-only turns that sandbox denies writes to
the workspace and the edit tools are off, and it cannot read the run store. If the host is
itself sandboxed, Claude's sandbox cannot start inside it, so the host's sandbox does the
confining. That sandbox allows writes to the workspace, which is why read-only turns there use
plan mode and rely on the restore described next.

## Read-only turns

Every peer turn without `--write` is meant to leave the source files alone. The runner compares
the files before and after. If they changed, the turn's verdict is dropped and `peer` exits 8.

Whether the change is also undone depends on the peer:

- **Claude peer.** The runner snapshots the files first (copy-on-write clones on APFS, plain
  copies elsewhere) and restores them afterwards: edits reverted, created files removed, deleted
  files returned.
- **Codex peer.** Codex's sandbox blocks writes at the operating-system level, so no snapshot is
  taken and a change is reported but not undone. `DUET_SNAPSHOT=always` forces a snapshot. The
  sandbox does not confine MCP servers set up in your Codex configuration, so a write made
  through one is in this category.

Files git ignores, caches, the run store, and anything outside the workspace are never restored.
Above `DUET_SNAPSHOT_MAX_MB` (default 1024) of source files no snapshot is taken at all.

### Read-only recovery

The changed versions are saved in `turns/<n>/readonly-violation/` as `changes.txt`, `files/` and
`changes.patch`. Read them, then look at the workspace: do not assume it was restored. Taking
the snapshot, saving a changed file, or restoring can each fail, and the runner reports what it
could not do. If the workspace still differs after a restore, the snapshot is kept in
`turns/<n>/snapshot-kept`.

The runner cannot tell the peer's change from an edit you or the host made during the turn, so
never edit while a peer turn is running.

If the change was only by-products of running tests, add them to `.gitignore`, run `check`, and
ask for the review again. Otherwise sort out what remains and then ask for another review. A
failed turn can exit 5 with a violation recorded; the same steps apply.

## Safeguards, and where they stop

- **Messages travel on standard input**, never in a command line, and each is wrapped in a tag
  carrying a random per-run marker, so text inside a message cannot pose as another speaker.
- **Credentials are stripped from the peer's environment.** Variables ending in `_TOKEN`,
  `_SECRET`, `_API_KEY` or `_PASSWORD` are dropped unless they belong to the peer's own client.
  `DUET_KEEP_ENV=NAME,NAME` keeps ones a build needs.
- **State is written under a lock**, and each command re-reads it and changes only its own
  fields, so a long check cannot overwrite a turn saved in the meantime.
- **The record is sealed.** After each write the runner stores a SHA-256 of `state.json` and
  `messages.jsonl` outside the workspace, and every command checks it first. An edited approval
  or a deleted seal stops everything with exit 9 until you reseal. This detects tampering and
  does not prevent it: a peer that rewrites the seal as well is not caught, and the transcript,
  `turns/` and `checks/` are not covered.
- **The blind round prevents accidental anchoring, not a determined reader.** The sealed
  proposal is encoded, not encrypted.
- **Both sandboxes let the peer read most of the disk.** Only a Claude peer in its own sandbox
  is denied the run store. A Codex peer is told not to look.

What counts as "the files": everything git tracks or would track when the workspace is the top
of a repository, and a walk of the folder otherwise. Ignored files and tool caches
(`__pycache__`, `node_modules`, `.venv` and the like) are left out. Every file is hashed by its
full contents.

## Things to know before you rely on it

- **Everything in the conversation goes to the other lab's model**, along with any file the peer
  reads in the workspace. Keep secrets out of the goal and the workspace.
- **Peer turns use your Codex or Claude plan** at that client's configured model and effort. A
  long run at maximum effort is expensive. `--peer-effort`, `--max-turns` and `--max-cost` are
  the controls. If an API key for the peer's lab is set in your shell, the client may bill that
  key instead of the plan, and `init` warns about it.
- **There is no evidence yet that the pair beats one model working alone on the same budget.**
  `eval/` holds a harness for that comparison and a smaller benchmark that tests whether a
  reviewer from the other lab catches defects that a same-lab reviewer misses. Neither has
  produced results worth quoting. See `eval/README.md`.
- **Only Claude and Codex are supported as hosts.** Another tool can load the skill, but the
  runner will label it as one of those two.
- **The quality flags and the engineering standard do not make code good.** They catch a short
  list of specific shortcuts. The rest still depends on two models reviewing each other.

## Tests

```sh
python3 -m pytest tests -q
```

The suite uses local stand-ins for both clients, never calls a model, and keeps its runs in a
temporary `DUET_HOME`.

## License

MIT. See [LICENSE](LICENSE).
