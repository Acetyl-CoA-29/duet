"""Tests for the review micro-benchmark in eval/reviewbench. Stand-in clients only: no model is ever called."""
from __future__ import annotations

import json
import stat
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "eval"))

from evallib import arms  # noqa: E402
from reviewbench import analysis, bench, cli, clients, parse, plan, prompts, report, score, store, verify  # noqa: E402
from reviewbench import cases as caselib  # noqa: E402

FAKE = Path(__file__).resolve().parent / "fake_review_cli.py"
PADDING = "".join(f"VALUE_{n} = {n}\n" for n in range(40))
MODULE = '"""Add things."""\n\n\ndef add(a, b):\n    return a - b\n\n\n' + PADDING
FIXED = MODULE.replace("a - b", "a + b")
TESTS = ("from module import add\n\n\ndef test_happy_zero():\n    assert add(0, 0) == 0\n\n\n"
         "def test_defect_sum():\n    assert add(1, 2) == 3\n")
KEY = {"class": "wrong-operator", "lines": [[5, 5]], "statement": "add subtracts instead of adding.",
       "keywords": ["subtract", "minus"]}
CAUGHT = "DEFECTS:\n1. L5: add subtracts b instead of adding it."


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def make_case(root: Path, name: str = "seeded-one", key: Optional[Dict[str, Any]] = KEY, module: str = MODULE,
              fixed: Optional[str] = FIXED, tests: str = TESTS, description: str = "Adds two numbers.") -> Path:
    folder = root / name
    write(folder / "module.py", module)
    write(folder / "test_hidden.py", tests)
    write(folder / "case.json", json.dumps({"description": description, "key": key}))
    if fixed is not None:
        write(folder / "fixed.py", fixed)
    return folder


def make_control(root: Path, name: str = "control-one") -> Path:
    clean_tests = "from module import add\n\n\ndef test_happy_sum():\n    assert add(1, 2) == 3\n"
    return make_case(root, name, key=None, module=FIXED, fixed=None, tests=clean_tests)


@pytest.fixture()
def cases_dir(tmp_path: Path) -> Path:
    root = tmp_path / "cases"
    make_case(root)
    make_control(root)
    return root


class Fake:
    def __init__(self, log: Path, script: Path) -> None:
        self.log, self._script = log, script

    def script(self, rules: List[Dict[str, Any]], default: str = "DEFECTS: NONE") -> None:
        write(self._script, json.dumps({"default": default, "rules": rules}))

    def calls(self) -> List[Dict[str, Any]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]


@pytest.fixture()
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Fake:
    FAKE.chmod(FAKE.stat().st_mode | stat.S_IXUSR)
    stand_in = Fake(tmp_path / "calls.jsonl", tmp_path / "script.json")
    monkeypatch.setenv(arms.CLAUDE_BIN_ENV, str(FAKE))
    monkeypatch.setenv(arms.CODEX_BIN_ENV, str(FAKE))
    monkeypatch.setenv("FAKE_REVIEW_LOG", str(stand_in.log))
    monkeypatch.setenv("FAKE_REVIEW_SCRIPT", str(tmp_path / "script.json"))
    stand_in.script([])
    return stand_in


def run_options(tmp_path: Path, cases_dir: Path, **changes: Any) -> bench.RunOptions:
    base: Dict[str, Any] = {"session_dir": tmp_path / "results" / "s1", "work_root": tmp_path / "scratch" / "work",
                            "client": clients.ClientOptions(deny_read=(cases_dir, tmp_path / "results")),
                            "max_calls": 100, "max_total_cost": None}
    base.update(changes)
    return bench.RunOptions(**base)


def run_all(tmp_path: Path, cases_dir: Path, **changes: Any) -> bench.Outcome:
    found = caselib.discover(cases_dir)
    return bench.run_plan(plan.make_plan(found, 1, 7), {case.name: case for case in found},
                          run_options(tmp_path, cases_dir, **changes), lambda text: None)


def cli_run(tmp_path: Path, cases_dir: Path, *extra: str) -> int:
    return cli.main(["run", "--cases", "all", "--cases-dir", str(cases_dir), "--results", str(tmp_path / "results"),
                     "--session", "s1", "--work-dir", str(tmp_path / "scratch" / "work"), *extra])


# ---------------------------------------------------------------- cases

def test_shipped_cases_load_and_are_the_promised_mix() -> None:
    found = caselib.discover(caselib.DEFAULT_CASES_DIR)
    seeded = [case for case in found if not case.control]
    assert 16 <= len(seeded) <= 20 and 3 <= len(found) - len(seeded) <= 4
    assert len({case.key.defect_class for case in seeded if case.key}) >= 12
    for case in found:
        assert caselib.MIN_MODULE_LINES <= case.line_count <= caselib.MAX_MODULE_LINES
        assert (case.root / "fixed.py").is_file() != case.control


def test_load_case_reads_key_and_control(cases_dir: Path) -> None:
    seeded = caselib.load_case(cases_dir / "seeded-one")
    assert seeded.key is not None and seeded.key.lines == ((5, 5),) and seeded.key.keywords == ("subtract", "minus")
    assert not seeded.control and caselib.load_case(cases_dir / "control-one").control
    assert [case.name for case in caselib.discover(cases_dir, ["seeded-one", "seeded-one"])] == ["seeded-one"]
    with pytest.raises(caselib.CaseError, match="unknown case"):
        caselib.discover(cases_dir, ["nope"])
    with pytest.raises(caselib.CaseError, match="no such cases directory"):
        caselib.discover(cases_dir / "absent")


@pytest.mark.parametrize("change, message", [
    ({"key": {**KEY, "lines": [[5, 999]]}}, "inside the"),
    ({"key": {**KEY, "lines": []}}, "non-empty list"),
    ({"key": {**KEY, "keywords": ["subtract"]}}, "2 to 5"),
    ({"key": {**KEY, "keywords": ["Subtract", "minus"]}}, "lower-case"),
    ({"key": {**KEY, "keywords": ["subtract(", "minus"]}}, "not a valid pattern"),
    ({"key": {**KEY, "keywords": ["overflow", "leak"]}}, "disagree"),
    ({"key": {**KEY, "class": " "}}, "class"),
    ({"key": {**KEY, "statement": ""}}, "statement"),
    ({"key": "yes"}, "must be an object"),
    ({"description": " "}, "description"),
    ({"module": "x = 1\n"}, "lines; keep it between"),
    ({"module": MODULE + "# TODO: fix the bug above\n"}, "could point a reviewer"),
    ({"fixed": None}, "needs fixed.py"),
])
def test_load_case_rejects_malformed_cases(tmp_path: Path, change: Dict[str, Any], message: str) -> None:
    with pytest.raises(caselib.CaseError, match=message):
        caselib.load_case(make_case(tmp_path, **change))


def test_load_case_rejects_broken_files(tmp_path: Path) -> None:
    folder = make_case(tmp_path)
    write(folder / "case.json", "{nope")
    with pytest.raises(caselib.CaseError, match="not valid JSON"):
        caselib.load_case(folder)
    write(folder / "case.json", "[1]")
    with pytest.raises(caselib.CaseError, match="JSON object"):
        caselib.load_case(folder)
    write(folder / "case.json", json.dumps({"description": "Adds."}))
    with pytest.raises(caselib.CaseError, match="or null"):
        caselib.load_case(folder)
    write(folder / "case.json", json.dumps({"description": "Adds.", "key": None}))
    with pytest.raises(caselib.CaseError, match="must not have"):
        caselib.load_case(folder)
    (folder / "test_hidden.py").unlink()
    with pytest.raises(caselib.CaseError, match="missing test_hidden.py"):
        caselib.load_case(folder)


# ---------------------------------------------------------------- verify

def test_verify_accepts_a_sound_seeded_case_and_control(cases_dir: Path, tmp_path: Path) -> None:
    for case in caselib.discover(cases_dir):
        assert verify.verify_case(case, tmp_path / "scratch") == []


def test_verify_command_passes_on_every_shipped_case(capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["verify"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "All cases behave as designed." in out and "WRONG" not in out and out.count("ok  ") >= 19


def test_verify_reports_each_way_a_case_can_be_wrong(tmp_path: Path) -> None:
    def problems(name: str, **change: Any) -> str:
        return " | ".join(verify.verify_case(caselib.load_case(make_case(tmp_path / "c", name, **change)), tmp_path / "s"))

    assert "does not detect the defect" in problems("harmless", module=FIXED + "\n", fixed=FIXED)
    assert "does not pass on the module as shown" in problems("breaks-happy", module=MODULE.replace("a - b", "a - b - 1"))
    assert "corrected module does not pass" in problems("bad-fix", fixed=MODULE + "\n")
    assert "identical" in problems("same", fixed=MODULE)
    assert "do not cover" in problems("wrong-lines", key={**KEY, "lines": [[30, 31]]})
    assert "nothing shows the defect is real" in problems("no-defect-test", tests=TESTS.split("def test_defect")[0])
    assert "no test_happy_" in problems("no-happy-test", tests="from module import add\n\n\ndef test_defect_sum():\n    assert add(1, 2) == 3\n")
    control = caselib.load_case(make_case(tmp_path / "c", "bad-control", key=None, fixed=None))
    assert "a control must pass" in " ".join(verify.verify_case(control, tmp_path / "s"))


def test_verify_command_fails_and_names_the_problem(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    make_case(tmp_path / "cases", "wrong-lines", key={**KEY, "lines": [[30, 31]]})
    assert cli.main(["verify", "--cases-dir", str(tmp_path / "cases")]) == cli.EXIT_FAILED
    out = capsys.readouterr().out
    assert "WRONG   wrong-lines" in out and "1 problem(s)." in out


def test_changed_span_handles_edits_and_insertions() -> None:
    assert verify.changed_span("a\nb\nc\n", "a\nB\nc\n") == (2, 2)
    assert verify.changed_span("a\nb\nc\n", "a\nb\nnew\nc\n") == (2, 3)
    assert verify.changed_span("a\n", "a\n") is None


# ---------------------------------------------------------------- prompts

def test_review_prompt_shows_the_module_and_nothing_hidden() -> None:
    for case in caselib.discover(caselib.DEFAULT_CASES_DIR):
        for text in (prompts.review_prompt(case), prompts.restate_prompt(case)):
            assert case.description in text and prompts.numbered(case.module) in text
            assert case.name not in text and str(case.root) not in text
            for hidden in ("fixed.py", "test_hidden", "case.json", "seeded", "test_defect", "control"):
                assert hidden not in text
            if case.key:
                assert case.key.statement not in text and case.key.defect_class not in text


def test_review_prompt_is_the_same_whatever_the_condition(cases_dir: Path) -> None:
    case = caselib.load_case(cases_dir / "seeded-one")
    assert prompts.review_prompt(case) == prompts.review_prompt(caselib.load_case(cases_dir / "seeded-one"))
    assert "DEFECTS: NONE" in prompts.review_prompt(case) and "may have no defects" in prompts.review_prompt(case)
    assert "DEFECTS" not in prompts.restate_prompt(case) and "do not assess it" in prompts.restate_prompt(case)
    assert prompts.numbered("a\n\nb") == "1 | a\n2 |\n3 | b"


# ---------------------------------------------------------------- parsing

def test_parse_none_and_numbered_lists() -> None:
    assert parse.parse_review("DEFECTS: NONE") == parse.Review(parse.NONE)
    assert parse.parse_review("**Defects: none.**").verdict == parse.NONE
    review = parse.parse_review("DEFECTS:\n1. L12: first thing.\n2. L20-L24: second\n   continues here.\n\nThanks!")
    assert review.verdict == parse.DEFECTS and not review.conflict
    assert [finding.lines for finding in review.findings] == [((12, 12),), ((20, 24),)]
    assert review.findings[1].text == "L20-L24: second continues here."


@pytest.mark.parametrize("text, expected", [
    ("L7: x", ((7, 7),)), ("Lines 9 to 11 and line 30: x", ((9, 11), (30, 30))), ("line 14 - x", ((14, 14),)),
    ("L30-L12: reversed", ((12, 30),)), ("`L5`–`L6`".replace("`", ""), ((5, 6),)), ("12-14: bare", ((12, 14),)),
    ("no line here", ()), ("L3 and again L3", ((3, 3),)), ("lines 4 through 6", ((4, 6),)),
])
def test_line_refs(text: str, expected: Any) -> None:
    assert parse.line_refs(text) == expected


def test_parse_tolerates_decoration_and_inline_lists() -> None:
    review = parse.parse_review("Here is my review.\n\n## DEFECTS: 1) **L3**: inline item\n- L8-9: bullet item\n- a prose bullet\n(3) `line 15`: third")
    assert [finding.lines for finding in review.findings] == [((3, 3),), ((8, 9),), ((15, 15),)]
    assert "a prose bullet" in review.findings[1].text


def test_parse_malformed_conflicting_and_overlong_replies() -> None:
    assert parse.parse_review("").verdict == parse.UNPARSED
    assert parse.parse_review("I think line 5 looks suspicious but I am not sure.").verdict == parse.UNPARSED
    assert parse.parse_review("DEFECTS:\n").verdict == parse.UNPARSED
    conflict = parse.parse_review("DEFECTS: NONE\n1. L5: actually this.")
    assert conflict.verdict == parse.DEFECTS and conflict.conflict and len(conflict.findings) == 1
    many = parse.parse_review("DEFECTS:\n" + "\n".join(f"{n}. L{n}: item" for n in range(1, 9)))
    assert len(many.findings) == prompts.MAX_ITEMS and many.dropped == 3


# ---------------------------------------------------------------- scoring

def scored(case: caselib.Case, reply: str) -> score.Score:
    return score.score_review(case, parse.parse_review(reply))


def test_strict_needs_line_and_keyword_in_one_item(cases_dir: Path) -> None:
    case = caselib.load_case(cases_dir / "seeded-one")
    hit = scored(case, CAUGHT)
    assert hit.strict and hit.lenient and hit.line_hit and hit.keyword_hit and not hit.false_alarm
    line_only = scored(case, "DEFECTS:\n1. L5: something is off here.")
    assert not line_only.strict and line_only.lenient and line_only.line_hit and not line_only.keyword_hit
    word_only = scored(case, "DEFECTS:\n1. L30: uses minus somewhere.")
    assert not word_only.strict and word_only.lenient and word_only.keyword_hit and not word_only.line_hit
    split = scored(case, "DEFECTS:\n1. L5: something is off.\n2. L30: it subtracts.")
    assert not split.strict and split.lenient and split.reported == 2
    assert not scored(case, "DEFECTS:\n1. L30: unrelated.").lenient
    assert scored(case, "DEFECTS: NONE") == score.Score(False, True, 0)
    unparsed = scored(case, "It subtracts on line 5, I believe.")
    assert not unparsed.parsed and not unparsed.strict and not unparsed.lenient


def test_line_tolerance_and_overlong_ranges(cases_dir: Path) -> None:
    case = caselib.load_case(cases_dir / "seeded-one")
    assert score.LINE_TOLERANCE == 2
    assert scored(case, "DEFECTS:\n1. L7: it subtracts.").strict and scored(case, "DEFECTS:\n1. L3: it subtracts.").strict
    assert not scored(case, "DEFECTS:\n1. L8: it subtracts.").strict
    assert scored(case, "DEFECTS:\n1. L1-L4: it subtracts.").strict
    assert score.score_review(case, parse.parse_review("DEFECTS:\n1. L8: it subtracts."), tolerance=3).strict
    whole_file = scored(case, "DEFECTS:\n1. L1-L47: it subtracts.")
    assert not whole_file.strict and not whole_file.line_hit and whole_file.lenient


def test_any_report_on_a_control_is_a_false_alarm(cases_dir: Path) -> None:
    control = caselib.load_case(cases_dir / "control-one")
    assert scored(control, "DEFECTS:\n1. L5: looks wrong.").false_alarm
    clean = scored(control, "DEFECTS: NONE")
    assert clean.control and not clean.false_alarm and not clean.strict and not clean.lenient
    assert not scored(control, "no idea").false_alarm and not scored(control, "no idea").parsed


# ---------------------------------------------------------------- clients and the run

def test_plan_counts_orders_and_maps_conditions(cases_dir: Path) -> None:
    found = caselib.discover(cases_dir)
    cells = plan.make_plan(found, 2, 11)
    assert len(cells) == 2 * 2 * 2 * 2 and plan.planned_calls(cells) == 24 and len(set(cells)) == len(cells)
    assert cells == plan.make_plan(found, 2, 11) and cells != plan.make_plan(found, 2, 12)
    assert plan.condition_cell("claude", plan.COND_SELF, "c", 1) == plan.Cell("c", "claude", "self", 1)
    assert plan.condition_cell("claude", plan.COND_SAME, "c", 1) == plan.Cell("c", "claude", "fresh", 1)
    assert plan.condition_cell("claude", plan.COND_CROSS, "c", 1) == plan.condition_cell("codex", plan.COND_SAME, "c", 1)
    assert 0 <= plan.new_order_seed() < plan.MAX_ORDER_SEED
    with pytest.raises(ValueError):
        plan.condition_cell("claude", "other", "c", 1)
    with pytest.raises(ValueError):
        clients.build_argv("gemini", clients.ClientOptions(), Path("x"))


def test_full_run_makes_read_only_calls_in_empty_folders(fake: Fake, cases_dir: Path, tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-real")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-real")
    fake.script([{"contains": "a - b", "step": "review", "reply": CAUGHT}])
    outcome = run_all(tmp_path, cases_dir)
    assert outcome == bench.Outcome(12, None, (), ())
    calls = fake.calls()
    assert len(calls) == 12 and sum(call["client"] == "claude" for call in calls) == 6
    for call in calls:
        assert call["files"] == [] and str(tmp_path / "scratch" / "work") in call["cwd"]
        assert "seeded-one" not in call["stdin"] and "subtracts instead" not in call["stdin"]
        if call["client"] == "claude":
            assert call["argv"][call["argv"].index("--tools") + 1] == "" and "--safe-mode" in call["argv"]
            assert call["argv"][call["argv"].index("--max-budget-usd") + 1] == "0.5"
            assert f"Read(/{cases_dir}/**)" in call["argv"][call["argv"].index("--settings") + 1]
            assert "OPENAI_API_KEY" not in call["secrets"] and "ANTHROPIC_API_KEY" in call["secrets"]
        else:
            assert 'sandbox_mode="read-only"' in call["argv"] and call["argv"][-1] == "-"
            assert "ANTHROPIC_API_KEY" not in call["secrets"] and "OPENAI_API_KEY" in call["secrets"]
    assert not list((tmp_path / "scratch" / "work").iterdir())
    session = tmp_path / "results" / "s1"
    assert "sk-" not in "".join(path.read_text() for path in session.rglob("*") if path.is_file())
    record = store.load_records(session)["seeded-one__claude__fresh__n1"]
    assert record["status"] == store.OK and record["reply"] == CAUGHT and record["calls"][0]["cost_usd"] == 0.05
    assert len(store.load_ledger(session)) == 12 and store.dollars_reported(store.load_ledger(session)) == pytest.approx(0.3)


def test_self_reviews_in_the_session_that_restated_and_prompts_match(fake: Fake, cases_dir: Path, tmp_path: Path) -> None:
    run_all(tmp_path, cases_dir)
    calls = fake.calls()
    for client in clients.REVIEWERS:
        mine = [call for call in calls if client == call["client"] and "a - b" in call["stdin"]]
        restate = next(call for call in mine if call["step"] == "restate")
        resumed = next(call for call in mine if call["resumed"])
        fresh = next(call for call in mine if call["step"] == "review" and not call["resumed"])
        assert restate["resumed"] is None and resumed["step"] == "review" and resumed["cwd"] == restate["cwd"]
        assert resumed["stdin"] == fresh["stdin"]
        record = store.load_records(tmp_path / "results" / "s1")[f"seeded-one__{client}__self__n1"]
        assert record["session"] == resumed["resumed"] and [call["step"] for call in record["calls"]] == ["restate", "review"]
    codex_resume = next(call for call in calls if call["client"] == "codex" and call["resumed"])
    assert codex_resume["argv"][:2] == ["exec", "resume"]
    fresh_prompts = {call["stdin"] for call in calls if call["step"] == "review" and "a - b" in call["stdin"]}
    assert len(fresh_prompts) == 1


def test_models_and_effort_reach_both_clients(tmp_path: Path) -> None:
    options = clients.ClientOptions(effort="low", claude_model="opus", codex_model="gpt-x", max_call_cost=None)
    claude = clients.claude_argv(options, "sess-1")
    assert claude[claude.index("--model") + 1] == "opus" and claude[claude.index("--effort") + 1] == "low"
    assert claude[-2:] == ["--resume", "sess-1"] and "--max-budget-usd" not in claude
    codex = clients.codex_argv(options, tmp_path / "last.txt")
    assert codex[codex.index("-m") + 1] == "gpt-x" and 'model_reasoning_effort="low"' in codex and "resume" not in codex
    assert clients.other("claude") == "codex" and clients.other("codex") == "claude"


def test_call_cap_stops_cleanly_and_resume_repeats_nothing(fake: Fake, cases_dir: Path, tmp_path: Path) -> None:
    first = run_all(tmp_path, cases_dir, max_calls=5)
    assert first.stopped is not None and "call cap" in first.stopped and first.calls <= 5 and first.undone
    made = len(fake.calls())
    assert made == first.calls == len(store.load_ledger(tmp_path / "results" / "s1"))
    done_before = {name for name, record in store.load_records(tmp_path / "results" / "s1").items() if record["status"] == store.OK}
    assert set(first.undone).isdisjoint(done_before)
    second = run_all(tmp_path, cases_dir, max_calls=12)
    assert second.stopped is None and second.undone == () and len(fake.calls()) == 12 and second.calls == 12 - made
    assert run_all(tmp_path, cases_dir, max_calls=12).calls == 0 and len(fake.calls()) == 12


def test_a_self_cell_never_starts_with_room_for_only_one_call(fake: Fake, cases_dir: Path, tmp_path: Path) -> None:
    case = caselib.load_case(cases_dir / "seeded-one")
    cell = plan.Cell(case.name, "codex", "self", 1)
    outcome = bench.run_plan([cell], {case.name: case}, run_options(tmp_path, cases_dir, max_calls=1), lambda text: None)
    assert outcome.calls == 0 and outcome.undone == (cell.id,) and fake.calls() == []


def test_dollar_cap_counts_claude_headroom_and_lets_codex_through(fake: Fake, cases_dir: Path, tmp_path: Path) -> None:
    options = run_options(tmp_path, cases_dir, max_total_cost=1.0)
    ledger = [{"cost_usd": 0.4}, {"cost_usd": None}, {"cost_usd": 0.1}]
    assert bench.cap_reason(plan.Cell("c", "claude", "fresh", 1), 1, ledger, options) is None
    assert "dollar cap" in str(bench.cap_reason(plan.Cell("c", "claude", "self", 1), 2, ledger, options))
    assert bench.cap_reason(plan.Cell("c", "codex", "self", 1), 2, ledger, options) is None
    assert "dollar cap" in str(bench.cap_reason(plan.Cell("c", "claude", "fresh", 1), 1, [{"cost_usd": 1.0}], options))
    fake.script([{"client": "claude", "cost": 0.3}])
    outcome = run_all(tmp_path, cases_dir, max_total_cost=1.0)
    assert outcome.stopped is not None and "dollar cap" in outcome.stopped
    assert store.dollars_reported(store.load_ledger(tmp_path / "results" / "s1")) <= 1.0


def test_failed_calls_are_recorded_counted_and_only_retried_on_request(fake: Fake, cases_dir: Path, tmp_path: Path) -> None:
    fake.script([{"client": "codex", "contains": "a - b", "step": "review", "exit": 3}])
    first = run_all(tmp_path, cases_dir)
    session = tmp_path / "results" / "s1"
    assert set(first.failed) == {"seeded-one__codex__fresh__n1", "seeded-one__codex__self__n1"} and first.calls == 12
    half_done = store.load_records(session)["seeded-one__codex__self__n1"]
    assert half_done["status"] == store.ERROR and half_done["restated"] and "exited with code 3" in half_done["error"]
    assert Path(half_done["workdir"]).is_dir() and bench.can_continue(half_done)
    assert run_all(tmp_path, cases_dir).calls == 0 and len(fake.calls()) == 12
    fake.script([])
    retried = run_all(tmp_path, cases_dir, retry_errors=True)
    assert retried.failed == () and retried.calls == 2
    again = [call for call in fake.calls()[12:]]
    assert [call["step"] for call in again] == ["review", "review"] and sorted(bool(call["resumed"]) for call in again) == [False, True]
    resumed = next(call for call in again if call["resumed"])
    assert resumed["resumed"] == half_done["session"] and resumed["cwd"].endswith(Path(half_done["workdir"]).name)
    finished = store.load_records(session)["seeded-one__codex__self__n1"]
    assert finished["status"] == store.OK and len(finished["calls"]) == 3 and not Path(half_done["workdir"]).exists()
    assert len(store.load_ledger(session)) == 14


def test_restate_failures_and_missing_session_ids_end_the_cell(fake: Fake, cases_dir: Path, tmp_path: Path) -> None:
    case = caselib.load_case(cases_dir / "seeded-one")
    options = run_options(tmp_path, cases_dir)
    fake.script([{"step": "restate", "no_session": True}])
    record = bench.run_cell(plan.Cell(case.name, "claude", "self", 1), case, options)
    assert record["status"] == store.ERROR and "no session id" in record["error"] and len(record["calls"]) == 1
    assert not bench.can_continue(record) and not Path(record["workdir"]).exists()
    fake.script([{"reply": "  "}])
    assert "empty reply" in bench.run_cell(plan.Cell(case.name, "codex", "fresh", 1), case, options)["error"]
    fake.script([{"sleep": 5}])
    slow = run_options(tmp_path, cases_dir, client=clients.ClientOptions(timeout=1))
    timed_out = bench.run_cell(plan.Cell(case.name, "claude", "fresh", 1), case, slow)
    assert timed_out["calls"][0]["timed_out"] and "limit" in timed_out["error"]


def test_call_reports_unusable_output_and_leaks(fake: Fake, cases_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    options = clients.ClientOptions(deny_read=(cases_dir,))
    fake.script([{"reply": f"I opened {cases_dir}/seeded-one/fixed.py to check."}])
    leaked = clients.call("codex", "prompt", tmp_path / "w", tmp_path / "logs", "1-review", options)
    assert leaked.ok and set(leaked.leaks) == {"fixed.py", str(cases_dir)} and leaked.commands_run == 0
    assert (tmp_path / "logs" / "1-review.prompt.md").read_text() == "prompt"
    monkeypatch.setenv(arms.CLAUDE_BIN_ENV, str(tmp_path / "no-such-client"))
    missing = clients.call("claude", "prompt", tmp_path / "w", tmp_path / "logs", "2-review", options)
    assert not missing.ok and "could not start" in missing.error
    quiet = write(tmp_path / "quiet.sh", "#!/bin/sh\ncat >/dev/null\necho not json\n")
    quiet.chmod(0o755)
    monkeypatch.setenv(arms.CLAUDE_BIN_ENV, str(quiet))
    assert "no result" in clients.call("claude", "prompt", tmp_path / "w", tmp_path / "logs", "3-review", options).error
    failing = write(tmp_path / "failing.sh", "#!/bin/sh\ncat >/dev/null\n"
                    "echo '{\"type\": \"result\", \"is_error\": true, \"result\": \"credit balance too low\"}'\n")
    failing.chmod(0o755)
    monkeypatch.setenv(arms.CLAUDE_BIN_ENV, str(failing))
    assert "credit balance" in clients.call("claude", "prompt", tmp_path / "w", tmp_path / "logs", "4-review", options).error


def test_interrupt_stops_the_run_and_lists_what_is_left(cases_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupted(*args: Any, **kwargs: Any) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(bench.clients, "call", interrupted)
    outcome = run_all(tmp_path, cases_dir)
    assert outcome.stopped is not None and "interrupted" in outcome.stopped and len(outcome.undone) == 8


def test_store_skips_unreadable_files(tmp_path: Path) -> None:
    session = tmp_path / "s"
    store.write_record(session, {"cell": "a", "status": store.OK})
    write(session / store.CELLS / "broken.json", "{nope")
    write(session / store.CELLS / "odd.json", "[1]")
    assert list(store.load_records(session)) == ["a"]
    write(session / store.LEDGER, '{"cost_usd": 0.5}\nnot json\n[1]\n{"cost_usd": true}\n')
    assert store.calls_made(store.load_ledger(session)) == 2 and store.dollars_reported(store.load_ledger(session)) == 0.5
    assert store.load_ledger(tmp_path / "absent") == [] and store.load_invocations(session) == []
    assert store.is_session(session) and not store.is_session(tmp_path)


# ---------------------------------------------------------------- the command line

def test_dry_run_prints_the_exact_call_count_and_starts_nothing(fake: Fake, cases_dir: Path, tmp_path: Path,
                                                                capsys: pytest.CaptureFixture) -> None:
    assert cli_run(tmp_path, cases_dir, "--dry-run", "--show-prompt") == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "= 8 cell(s), 12 call(s) in the full plan" in out and "claude 6, codex 6 = 12" in out
    assert "Dry run: 12 call(s) would be made, nothing was started." in out
    assert "--tools ''" in out and 'sandbox_mode="read-only"' in out and "    | DEFECTS: NONE" in out
    assert fake.calls() == [] and not (tmp_path / "results").exists()
    assert cli.main(["run", "--cases", "all", "--dry-run"]) == cli.EXIT_OK
    assert "22 case(s) (18 seeded, 4 control)" in capsys.readouterr().out


def test_run_command_stops_at_a_cap_then_resumes_and_reports(fake: Fake, cases_dir: Path, tmp_path: Path,
                                                             capsys: pytest.CaptureFixture) -> None:
    fake.script([{"contains": "a - b", "step": "review", "client": "codex", "reply": CAUGHT}])
    assert cli_run(tmp_path, cases_dir, "--max-calls", "4") == cli.EXIT_FAILED
    out = capsys.readouterr().out
    assert "call cap" in out and "left undone. Resume with the same command and --session s1" in out and "INCOMPLETE" in out
    made = len(fake.calls())
    assert 0 < made <= 4
    assert cli_run(tmp_path, cases_dir, "--dry-run") == cli.EXIT_OK
    assert f"calls already made in this session: {made}" in capsys.readouterr().out
    assert cli_run(tmp_path, cases_dir) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert len(fake.calls()) == 12 and "INCOMPLETE" not in out and "Verdict: TOO FEW" in out
    manifest = store.load_invocations(tmp_path / "results" / "s1")
    assert len(manifest) == 2 and manifest[0]["stopped"] and manifest[1]["stopped"] is None
    assert manifest[0]["order_seed"] == manifest[1]["order_seed"] and manifest[1]["calls"] == 12 - made
    assert cli.main(["summary", "--results", str(tmp_path / "results"), "--cases-dir", str(cases_dir)]) == cli.EXIT_OK
    summary = capsys.readouterr().out
    assert "session s1" in summary and "1 seeded case(s), 1 control(s)" in summary and "$0.30" in summary


def test_run_command_refuses_bad_arguments_and_layouts(fake: Fake, cases_dir: Path, tmp_path: Path,
                                                       capsys: pytest.CaptureFixture) -> None:
    assert cli_run(tmp_path, cases_dir, "--samples", "0") == cli.EXIT_USAGE
    assert cli_run(tmp_path, cases_dir, "--max-calls", "0") == cli.EXIT_USAGE
    assert cli_run(tmp_path, cases_dir, "--max-total-cost", "0") == cli.EXIT_USAGE
    assert cli.main(["run", "--cases", " , ", "--dry-run"]) == cli.EXIT_USAGE
    assert cli.main(["run", "--cases", "all", "--results", str(REPO / "eval" / "results"), "--session", "x",
                     "--cases-dir", str(cases_dir)]) == cli.EXIT_USAGE
    assert "inside the repository" in capsys.readouterr().err
    assert cli.main(["run", "--cases", "all", "--cases-dir", str(cases_dir), "--results", str(tmp_path / "results"),
                     "--work-dir", str(tmp_path / "work")]) == cli.EXIT_USAGE
    assert "too close" in capsys.readouterr().err
    assert fake.calls() == []
    assert cli_run(tmp_path, cases_dir, "--max-calls", "1", "--order-seed", "3") == cli.EXIT_FAILED
    assert cli_run(tmp_path, cases_dir, "--effort", "high") == cli.EXIT_USAGE
    assert "different effort" in capsys.readouterr().err
    assert cli.main(["summary", "--results", str(tmp_path / "nothing-here")]) == cli.EXIT_FAILED


def test_list_command(capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["list"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "s04-tally\t49 lines\trace-non-atomic-update" in out and "18 seeded case(s), 4 clean control(s)" in out


# ---------------------------------------------------------------- analysis and the report

SHIPPED = caselib.discover(caselib.DEFAULT_CASES_DIR)
SEEDED = [case for case in SHIPPED if not case.control]


def reply_for(case: caselib.Case, kind: str) -> str:
    """A reply that scores as a strict catch ("hit"), a line-only match ("line") or nothing."""
    if case.key is None or kind == "miss":
        return "DEFECTS: NONE"
    text = case.key.statement if kind == "hit" else "something here looks questionable."
    return f"DEFECTS:\n1. L{case.key.lines[0][0]}: {text}"


def fill(session: Path, outcome: Callable[[plan.Cell, int], str], cases: Any = None, samples: int = 1) -> Path:
    """Write a finished session in which `outcome(cell, index of the seeded case)` picks each reply."""
    cases = SHIPPED if cases is None else cases
    for cell in plan.make_plan(cases, samples, 1):
        case = next(case for case in cases if case.name == cell.case)
        index = SEEDED.index(case) if case in SEEDED else -1
        store.write_record(session, {"cell": cell.id, "case": cell.case, "reviewer": cell.reviewer, "mode": cell.mode,
                                     "sample": cell.sample, "status": store.OK, "calls": [],
                                     "reply": reply_for(case, outcome(cell, index))})
    return session


def verdicts(session: Path, cases: Any = None, samples: int = 1) -> Dict[str, Any]:
    cases = SHIPPED if cases is None else cases
    found = analysis.score_session(store.load_records(session), {case.name: case for case in cases})
    pairs = {author: analysis.paired(found, author, cases, samples) for author in clients.REVIEWERS}
    return {"verdict": analysis.verdict(list(pairs.values())), **pairs}


def test_each_lab_covering_the_others_blind_spot_supports_the_premise(tmp_path: Path) -> None:
    result = verdicts(fill(tmp_path, lambda cell, i: "hit" if (i < 9) == (cell.reviewer == "claude") else "miss"))
    assert result["verdict"] == analysis.SUPPORTS
    claude = result["claude"]
    assert claude.units == 18 and claude.net == 9 and claude.rescued_same == frozenset() and len(claude.only_cross) == 9
    assert claude.only_same == claude.caught[plan.COND_SELF] and claude.p_value == pytest.approx(2 / 2 ** 9)
    assert result["codex"].net == 9


def test_identical_reviewers_count_against_the_premise(tmp_path: Path) -> None:
    result = verdicts(fill(tmp_path, lambda cell, i: "hit" if i < 9 else "miss"))
    assert result["verdict"] == analysis.AGAINST and result["claude"].net == 0 and result["claude"].p_value is None


def test_one_stronger_reviewer_is_not_support(tmp_path: Path) -> None:
    def outcome(cell: plan.Cell, i: int) -> str:
        return "hit" if i < (12 if (cell.reviewer, cell.mode) == ("codex", "fresh") else 6) else "miss"

    result = verdicts(fill(tmp_path, outcome))
    assert result["verdict"] == analysis.STRONGER and result["claude"].net == 6 and result["codex"].net == -6


def test_small_differences_and_missing_data_decide_nothing(tmp_path: Path) -> None:
    def outcome(cell: plan.Cell, i: int) -> str:
        return "hit" if i < (10 if (cell.reviewer, cell.mode) == ("codex", "fresh") else 9) else "miss"

    result = verdicts(fill(tmp_path / "a", outcome))
    assert result["verdict"] == analysis.INCONCLUSIVE and (result["claude"].net, result["codex"].net) == (1, -1)
    few = SHIPPED[:9]
    assert verdicts(fill(tmp_path / "b", lambda cell, i: "hit", few), few)["verdict"] == analysis.TOO_FEW
    assert analysis.verdict([result["claude"]]) == analysis.TOO_FEW


def test_sign_test_values() -> None:
    assert analysis.sign_test(0, 0) is None and analysis.sign_test(3, 0) == pytest.approx(0.25)
    assert analysis.sign_test(6, 0) == pytest.approx(0.03125) and analysis.sign_test(2, 2) == 1.0


def test_report_shows_rates_sets_verdict_and_usage(tmp_path: Path) -> None:
    def outcome(cell: plan.Cell, i: int) -> str:
        if i < 0:
            return "hit" if cell == plan.Cell("k01-lru-cache", "codex", "fresh", 1) else "miss"
        return "hit" if (i < 9) == (cell.reviewer == "claude") else "miss"

    session = fill(tmp_path / "s9", outcome)
    control = next(case for case in SHIPPED if case.name == "k01-lru-cache")
    store.write_record(session, {"cell": "k01-lru-cache__codex__fresh__n1", "case": control.name, "reviewer": "codex",
                                 "mode": "fresh", "sample": 1, "status": store.OK, "calls": [],
                                 "reply": "DEFECTS: NONE\n" + "\n".join(f"{n}. L{n}: nit" for n in range(1, 8))})
    store.write_record(session, {"cell": "k02-intervals__claude__self__n1", "case": "k02-intervals", "reviewer": "claude",
                                 "mode": "self", "sample": 1, "status": store.ERROR, "calls": [], "reply": ""})
    for entry in ({"reviewer": "claude", "ok": True, "cost_usd": 1.25, "tokens": {"input_tokens": 1000, "output_tokens": 50}, "seconds": 4},
                  {"reviewer": "codex", "ok": False, "cost_usd": None, "tokens": {}, "seconds": 2, "leaks": ["fixed.py"], "commands_run": 2}):
        store.append_ledger(session, entry)
    store.write_manifest(session, [], {"settings": {"samples": 1}, "stopped": "call cap: test"})
    text = report.format_session(session, SHIPPED)
    assert "18 seeded case(s), 4 control(s), 1 sample(s) per cell" in text
    assert "claude  self         claude    9/18 (50%)" in text and "codex   fresh-same   codex     9/18 (50%)" in text
    assert "1/4 (25%)" in text and "net (cross - same): +9" in text and "sign test p = 0.00" in text
    assert "Decision rule: strict scoring says SUPPORTS, lenient says SUPPORTS." in text and "Verdict: SUPPORTS" in text
    assert "s01-settings-save" in text and "$1.25" in text and "none reported" in text and "1,050" in text
    for warning in ("INCOMPLETE", "1 cell(s) have no usable reply", "CONTAMINATED?", "ran shell commands",
                    "said DEFECTS: NONE and also listed items", "more items than allowed", "Noise floor: not measured"):
        assert warning in text


def test_report_flags_a_result_that_depends_on_the_scoring(tmp_path: Path) -> None:
    session = fill(tmp_path, lambda cell, i: "line" if (i < 9) == (cell.reviewer == "claude") else "miss")
    text = report.format_session(session, SHIPPED)
    assert "strict scoring says AGAINST, lenient says SUPPORTS" in text
    assert "Verdict: INCONCLUSIVE (the two scorings disagree" in text and "0/18 (0%)" in text


def test_samples_give_a_noise_floor_and_labelled_units(tmp_path: Path) -> None:
    def outcome(cell: plan.Cell, i: int) -> str:
        flaky = cell.reviewer == "codex" and cell.mode == "fresh" and i < 4 and cell.sample == 2
        return "miss" if flaky or i >= 9 else "hit"

    session = fill(tmp_path, outcome, samples=2)
    found = analysis.score_session(store.load_records(session), {case.name: case for case in SHIPPED})
    floor = analysis.retest_disagreement(found, SHIPPED, 2)
    assert floor[("codex", "fresh")] == (4, 18) and floor[("claude", "self")] == (0, 18)
    pair = analysis.paired(found, "codex", SHIPPED, 2)
    assert pair.units == 36 and "s01-settings-save#2" in pair.only_cross and "s01-settings-save#1" not in pair.only_cross
    text = report.format_session(session, SHIPPED)
    assert "2 sample(s) per cell" in text and "codex fresh: 4/18 (22%)" in text
    assert analysis.unit_label("c", 1, 1) == "c" and report.share(0, 0) == "-"
