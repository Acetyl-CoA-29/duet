"""Prove, without any model, that each case is what it claims to be.

A seeded case: on the module as shown, every `test_happy_*` passes and every `test_defect_*` fails
(the defect survives a happy-path test and is real); on the corrected module everything passes; and
the lines that differ between the two are the lines the answer key names. A control: everything
passes on the module as shown."""
from __future__ import annotations

import difflib
import re
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from evallib import proc, scoring

from .cases import FIXED_FILE, MODULE_FILE, TEST_FILE, Case, LineRange

TEST_TIMEOUT_SECONDS = 120
OUTCOME_LINE = re.compile(r"^(PASSED|FAILED|ERROR)\s+\S*?::(\w+)", re.MULTILINE)
HAPPY_PREFIX, DEFECT_PREFIX = "test_happy_", "test_defect_"
LOG_TAIL_CHARS = 1500
Outcomes = Dict[str, List[str]]       # test function name -> every outcome reported for it


def run_hidden_tests(case: Case, variant: str, scratch: Path) -> Tuple[Optional[int], Outcomes, str]:
    """Run the case's hidden tests against one variant of the module (`module.py` or `fixed.py`),
    which the tests import as `module`. Returns (exit code, outcomes per test, output tail)."""
    folder = scratch / f"{case.name}-{Path(variant).stem}"
    if folder.exists():
        shutil.rmtree(str(folder))
    folder.mkdir(parents=True)
    shutil.copyfile(str(case.root / variant), str(folder / MODULE_FILE))
    shutil.copyfile(str(case.root / TEST_FILE), str(folder / TEST_FILE))
    log = folder / "pytest.log"
    done = proc.run([sys.executable, "-m", "pytest", "-q", "-rA", "--tb=short", "-p", "no:cacheprovider", TEST_FILE],
                    folder, TEST_TIMEOUT_SECONDS, log, log.with_suffix(".err"), env=scoring.scrub_env())
    output = log.read_text(encoding="utf-8", errors="replace")
    outcomes: Outcomes = {}
    for outcome, name in OUTCOME_LINE.findall(output):
        outcomes = {**outcomes, name: [*outcomes.get(name, []), outcome]}
    return done.exit_code, outcomes, (output + done.error)[-LOG_TAIL_CHARS:]


def changed_span(module: str, fixed: str) -> Optional[LineRange]:
    """First and last line of the shown module that the correction touches. An insertion counts as
    touching the lines on either side of it. None when the two texts are the same."""
    old, new = module.splitlines(), fixed.splitlines()
    touched: List[int] = []
    for tag, first, last, _, _ in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        touched += [max(1, first), min(len(old), first + 1)] if first == last else [first + 1, last]
    return (min(touched), max(touched)) if touched else None


def _named(outcomes: Outcomes, prefix: str) -> Outcomes:
    return {name: seen for name, seen in outcomes.items() if name.startswith(prefix)}


def _check_shown(case: Case, scratch: Path) -> List[str]:
    """The module as the reviewer sees it: happy-path tests pass; on a seeded case the defect tests fail."""
    code, outcomes, tail = run_hidden_tests(case, MODULE_FILE, scratch)
    happy, defect = _named(outcomes, HAPPY_PREFIX), _named(outcomes, DEFECT_PREFIX)
    problems: List[str] = []
    if not happy:
        problems.append(f"no {HAPPY_PREFIX}* test ran (exit {code}): {tail.strip()[-300:]}")
    problems += [f"happy-path test {name} does not pass on the module as shown"
                 for name, seen in happy.items() if set(seen) != {"PASSED"}]
    if case.control:
        if code != 0 or defect:
            problems.append(f"a control must pass all its tests and have no {DEFECT_PREFIX}* test (exit {code})")
        return problems
    if not defect:
        problems.append(f"no {DEFECT_PREFIX}* test ran, so nothing shows the defect is real")
    problems += [f"defect test {name} passes on the module as shown, so it does not detect the defect"
                 for name, seen in defect.items() if "PASSED" in seen]
    return problems


def _check_fixed(case: Case, scratch: Path) -> List[str]:
    """The corrected module passes everything, and differs from the shown one where the key says."""
    assert case.key is not None
    code, outcomes, tail = run_hidden_tests(case, FIXED_FILE, scratch)
    problems: List[str] = []
    if code != 0 or any(set(seen) != {"PASSED"} for seen in outcomes.values()) or not outcomes:
        problems.append(f"the corrected module does not pass the hidden tests (exit {code}): {tail.strip()[-300:]}")
    span = changed_span(case.module, (case.root / FIXED_FILE).read_text(encoding="utf-8"))
    if span is None:
        problems.append("the corrected module is identical to the one shown")
    elif not any(first <= span[1] and span[0] <= last for first, last in case.key.lines):
        problems.append(f"the correction touches lines {span[0]}-{span[1]}, which the key's line ranges "
                        f"{list(case.key.lines)} do not cover")
    return problems


def verify_case(case: Case, scratch: Path) -> List[str]:
    """Everything wrong with one case; an empty list means it behaves as designed."""
    problems = _check_shown(case, scratch)
    return problems if case.control else problems + _check_fixed(case, scratch)
