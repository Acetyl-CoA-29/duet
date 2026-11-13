# duet

A skill that puts the top Claude model and the top OpenAI model in **one conversation** to
frame, argue, plan, build, test and iterate until an agreed acceptance command passes and both
models approve the result.

Inspired by [karpathy/llm-council](https://github.com/karpathy/llm-council), with two changes:
two participants instead of a council, and they build the thing rather than only answer a question.

## How it works

```
 you ──goal──▶ host model (Claude Code session, or Codex)
                    │  say / peer / check / finish
                    ▼
              scripts/duet.py ──stdin──▶ peer model's CLI (one resumed session)
                    │
        <workspace>/.duet/<run-id>/
          conversation.md   readable transcript of everything said
          messages.jsonl    the same, as the source of truth
          state.json        session id, turn count, last check, caps
          turns/NNN/        exact prompt and raw output of each peer turn
```

- **The host** is the model you are talking to. It takes part directly.
- **The peer** is the other lab's model, run through its own command-line client: `codex exec`
  for a Claude host, `claude -p` for a Codex host. The first turn opens a session; every later
  turn resumes that same session, so the peer remembers the whole conversation and is sent only
  what is new: the host's messages, anything you said, and acceptance results.
- **Models** are not hardcoded. The host is whatever model you are running; the peer uses its
  client's configured default unless you pass `--peer-model`.

### The loop

| Phase | What happens | Enforced by the runner |
|---|---|---|
| frame | Agree the goal and one acceptance command | A run needs an acceptance command or an explicit waiver |
| propose | Each model writes its own approach | Blind: the peer cannot be asked until the host's proposal is recorded, and the host's proposal is withheld from the peer |
| debate | Proposals revealed; each attacks, concedes, defends with evidence | |
| plan | One joint plan, driver and reviewer per task | |
| build | One model edits at a time | The peer is read-only unless the turn is given write access |
| test | The acceptance command runs; real output goes into the conversation | |
| review | The non-author reads the files and gives a verdict | An approval from a turn with write access is not counted |

`finish` succeeds only when the acceptance command last passed on the files exactly as they are
now, and both models' latest verdicts are approve on those same files (compared by content
fingerprint). Any later edit reopens the gate.

### Stop conditions

- **Turn cap**: 30 peer turns by default (`--max-turns`); raising it needs your say-so.
- **Stall**: three identical acceptance failures in a row block further peer turns until you weigh in.
- **Peer failure or timeout**: recorded in the conversation; the session is kept and resumed.

## Use

In Claude Code or Codex, ask for it: "run a duet with Codex to build X", or `/duet`.

By hand:

```sh
D="python3 ~/.agents/skills/duet/scripts/duet.py"
$D init --workspace . --goal-file goal.md --accept "python3 -m pytest -q"   # prints the run folder
$D say  --run <run> --phase propose --file my-proposal.md
$D peer --run <run> --phase propose            # peer's blind proposal
$D say  --run <run> --phase debate --file reply.md --verdict revise
$D peer --run <run> --phase debate
$D peer --run <run> --phase build --write      # peer drives a build turn
$D check  --run <run>                          # run the acceptance command
$D say  --run <run> --phase review --file review.md --verdict approve
$D peer --run <run> --phase review
$D finish --run <run>
$D status --run <run>                          # what is still blocking, as JSON
```

Other options: `--peer claude` (when the host is Codex), `--peer-model`, `--peer-effort`
(Codex reasoning effort for the run), `--waive-accept "<reason>"`, `accept --cmd` to replace the
acceptance command, `extend --turns N --reason "..."`.

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
- Read-only turns rely on the peer client's own sandbox (`sandbox_mode="read-only"` for Codex,
  plan mode for Claude).
- The run folder `.duet/` is written inside the workspace; add it to `.gitignore` if you do not
  want transcripts committed.
- The fingerprint covers files git tracks or would track; ignored files are not part of it.

## Tests

```sh
python3 -m pytest tests -q
```

The suite uses local stand-ins for both clients and never calls a model.
