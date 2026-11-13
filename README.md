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
  workspace, so it cannot edit the record, and neither peer's sandbox lets it write there. If
  that directory cannot be written, as inside a Codex sandbox started without
  `--add-dir ~/.local/state/duet`, `init` says so in one line and stores the run in
  `<workspace>/.duet/<run-id>` instead, guarded by the integrity record described below.
- **Models** are not hardcoded. The host is whatever model you are running; the peer uses its
  client's configured default unless you pass `--peer-model`.

### The loop

A run is a sequence the runner enforces: `frame` (optional) → `propose` → `plan` → `build` ↔
`review` → done. The current phase is kept in `state.json`; a `say` or `peer` that names a phase
which cannot follow it is refused with exit 6 and a message listing what is allowed.

| Phase | What happens | Enforced by the runner |
|---|---|---|
| frame | Agree the goal and one acceptance command | Only at the start. A run needs an acceptance command or an explicit waiver; the command locks when the run leaves this phase and changing it later needs your agreement |
| propose | Each model writes its own approach | Blind: the peer cannot be asked until the host's proposal is recorded; the host's proposal is withheld from the peer and stored encoded on disk until the peer has proposed |
| plan | Proposals revealed; each attacks, concedes, defends with evidence; they pick one design and an ordered task list with a driver and reviewer per task | Refused until both proposals exist |
| build | One model edits at a time | Refused until both models have spoken in plan. The peer is read-only unless the turn is given write access, which only build turns can have (a change in a read-only turn is undone) |
| review | Whoever did not write the latest change tries to break it and gives a verdict | Follows build only. Only review-phase verdicts count; the author's own approval is neither needed nor counted |

The rest of the rules, each one line:

- build and review alternate freely; that is the iterate loop.
- Nothing goes back to frame or propose. Going back to plan from build or review needs
  `--replan "<reason>"`, which is recorded, and build reopens once both have spoken in plan again.
- `check` runs the acceptance command and `experiment` runs an agreed command; both work in any
  phase, cost no peer turn and do not move the run. (There is no separate test phase.)
- You can speak in any phase (`say --speaker user`); your words never move the run.
- A failed or timed-out peer turn does not move the run. Its record says whether workspace files
  changed during it, because a turn can finish its work and still fail to report back.
- `--skip-proposals "<reason>"` at `init` lets a run go from frame straight to plan or build.
- `--phase debate` is still accepted and means `plan`; `--phase test` is refused with a pointer
  to `check`. Runs started before phases were tracked load as before: their phase is read from
  the last message either model recorded.

`finish` succeeds only when the acceptance command last passed on the files exactly as they are
now, and the model that did **not** make the latest change has approved those same files
(compared by content fingerprint). If nothing was changed, both must approve. Any later edit
reopens the gate. The runner tracks who wrote last by comparing the workspace around every
command; an edit it did not see is put down to the host.

`experiment --cmd "..."` runs a command the two models agreed would settle a disagreement and
puts its output into the conversation.

### Stop conditions

- **Turn cap**: 30 peer turns by default (`--max-turns`), failed turns included; raising it needs your say-so.
- **Cost cap**: `--max-cost <dollars>` stops peer turns once the peer's client has reported that
  much. The Claude client reports cost; Codex reports tokens only, shown in `status`.
- **Stall**: three acceptance failures in a row with the same output (ignoring numbers, memory
  addresses and temp paths) block further peer turns until you weigh in.
- **Peer failure or timeout**: recorded in the conversation; the peer's whole process group is
  stopped; the session is kept and resumed, with a note that messages may repeat.
  `peer --new-session` replays the conversation into a fresh session if the old one cannot resume.

### Safeguards

- Message text reaches the peer on standard input only, never in a command line.
- Each message is wrapped in a tag carrying a per-run random marker, so text inside a message
  (command output, quoted material) cannot pass itself off as another speaker.
- The peer's process does not inherit the other lab's API key or common deploy secrets
  (`GITHUB_TOKEN`, AWS, npm, PyPI, Hugging Face, OpenRouter). `DUET_KEEP_ENV=NAME,NAME` keeps
  ones a build needs.
- Run state is written under a file lock. `.duet/` in the workspace is never part of the
  fingerprint, and when it exists (an in-workspace run, or a goal file kept there) it is added to
  the repository's local git exclude list so transcripts are not committed by accident.
- **Read-only turns are undone, not only detected.** Before every peer turn without `--write`
  the runner copies the files the fingerprint covers into the turn folder (copy-on-write clones
  on APFS, plain copies elsewhere). If any differ afterwards it puts them back exactly: edits
  reverted, created files removed, deleted files returned, modes kept. Ignored files and the run
  store are never touched. What the peer did is saved in `turns/<n>/readonly-violation/`
  (`changes.txt`, `files/`, `changes.patch`), the conversation records it, any approval from
  that turn is dropped and `peer` exits 8. Above `DUET_SNAPSHOT_MAX_MB` (default 1024) of source
  files no copy is taken: a warning says so and a change is reported but left in place.
- **Integrity record for in-workspace runs.** After each write the runner stores the SHA-256 of
  `state.json` and `messages.jsonl` outside the workspace (in the temp directory, or
  `DUET_GUARD_DIR`), and every command checks them first. A hand-edit of an approval, of
  `last_check` or of anything else in those two files, or a deleted record, stops every command
  with exit 9 until you run `reseal --by-user "..."`. This detects tampering; it does not prevent
  it. A peer that reads the runner's source and rewrites the record as well is not caught, and
  `conversation.md`, `turns/` and `checks/` are not covered. Runs in the state directory need no
  record because the peer cannot write there.
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

Every read-only turn, in every row, also has the snapshot-and-restore backstop.

Both have been run live end to end. Notes for **Codex as host**:

- The Claude client needs the network and its own folder, which Codex's default sandbox blocks.
  Start Codex with `-c 'sandbox_workspace_write.network_access=true' --add-dir ~/.claude
  --add-dir ~/.local/state/duet`, or approve the `peer` command to run outside the sandbox. The
  runner refuses when it detects a Codex sandbox without network. Without the second
  `--add-dir` the run is stored in the workspace (weaker isolation, see above).
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

## Use

In Claude Code or Codex, ask for it: "run a duet with Codex to build X", or `/duet`.

By hand:

```sh
D="python3 ~/.agents/skills/duet/scripts/duet.py"
$D init --workspace . --goal-file /tmp/goal.md --accept "python3 -m pytest -q"   # prints the run folder
# the goal can also come on stdin; write message files into <run>/drafts/
$D say  --run <run> --phase propose --file <run>/drafts/proposal.md
$D peer --run <run> --phase propose            # peer's blind proposal
$D say  --run <run> --phase plan --file <run>/drafts/reply.md --verdict revise
$D peer --run <run> --phase plan               # critique, one design, ordered tasks
$D experiment --run <run> --cmd "python3 probe.py"   # settle a dispute by evidence
$D peer --run <run> --phase build --write      # peer drives a build turn
$D check  --run <run>                          # run the acceptance command
$D say  --run <run> --phase review --file <run>/drafts/review.md --verdict approve
$D finish --run <run>                          # the peer wrote, the host approved: done
$D status --run <run>                          # phase, what may follow, what is blocking (JSON)
$D say  --run <run> --phase plan --replan "the API has no prefix scan" --file <run>/drafts/replan.md
```

Other options: `--peer claude` (when the host is Codex), `--peer-model`, `--peer-effort`
(Codex reasoning effort for the run), `--peer-user-config` (Claude peer), `--max-cost`,
`--skip-proposals "<reason>"`, `peer --new-session`, `accept --by-user "<words>"`, `--waive-accept "<reason>"`, `accept --cmd` to replace the
acceptance command, `extend --turns N --reason "..."`, `reseal --by-user "<words>"`.

Exit codes: 0 done, 1 check or experiment failed, 2 usage or peer unreachable, 3 turn or cost
cap, 4 stalled, 5 peer turn failed, 6 phase out of order, 7 finish refused, 8 files
changed during a read-only turn (restored), 9 in-workspace record edited outside the runner,
70 internal fault.

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
- The fingerprint covers files git tracks or would track; ignored files are not part of it.
  Files over 100 MB are compared by size and modification time, symbolic links by their target.
- The blind round stops accidental anchoring, not a determined reader: the sealed proposal is
  encoded, not encrypted, in both store locations.
- There is no evidence yet that the pair beats one model working alone at the same budget.
  That needs a comparison harness, which is not built.

## Tests

```sh
python3 -m pytest tests -q
```

The suite uses local stand-ins for both clients, never calls a model, and keeps its runs in a
temporary `DUET_HOME`.
