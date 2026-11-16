"""Case format: load and validate one small module with (at most) one seeded defect.

A case directory holds:
  module.py       what the reviewer is shown
  case.json       {"description": "<what the module is meant to do>", "key": null | {answer key}}
  test_hidden.py  never shown: `test_happy_*` pass on module.py, `test_defect_*` fail on it
  fixed.py        never shown: the corrected module (seeded cases only)

The answer key is {"class", "lines": [[first, last], ...], "statement", "keywords": [regex, ...]}.
A case whose key is null is a clean control: anything a reviewer reports on it is a false alarm."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

DEFAULT_CASES_DIR = Path(__file__).resolve().parent / "cases"
MODULE_FILE, FIXED_FILE, TEST_FILE, CASE_FILE = "module.py", "fixed.py", "test_hidden.py", "case.json"
HIDDEN_FILES = (FIXED_FILE, TEST_FILE, CASE_FILE)
MIN_MODULE_LINES, MAX_MODULE_LINES = 40, 150
MIN_KEYWORDS, MAX_KEYWORDS = 2, 5
# Words that would tell a reviewer where to look. None may appear in a module.
HINT_WORDS = re.compile(r"\b(bug|bugs|buggy|defect|seeded|fixme|todo|xxx|hack|broken|vulnerab\w*)\b", re.IGNORECASE)
LineRange = Tuple[int, int]


class CaseError(Exception):
    """A case directory is missing something or is malformed."""


@dataclass(frozen=True)
class AnswerKey:
    defect_class: str
    lines: Tuple[LineRange, ...]
    statement: str
    keywords: Tuple[str, ...]


@dataclass(frozen=True)
class Case:
    name: str
    root: Path
    description: str
    module: str
    key: Optional[AnswerKey]

    @property
    def control(self) -> bool:
        return self.key is None

    @property
    def line_count(self) -> int:
        return len(self.module.splitlines())


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CaseError(f"{path}: cannot be read ({exc})") from exc
    except json.JSONDecodeError as exc:
        raise CaseError(f"{path}: not valid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise CaseError(f"{path}: expected a JSON object")
    return data


def _line_ranges(raw: Any, where: str, line_count: int) -> Tuple[LineRange, ...]:
    if not isinstance(raw, list) or not raw:
        raise CaseError(f"{where}: \"lines\" must be a non-empty list of [first, last] pairs")
    ranges: List[LineRange] = []
    for pair in raw:
        numbers_ok = (isinstance(pair, list) and len(pair) == 2
                      and all(isinstance(n, int) and not isinstance(n, bool) for n in pair))
        if not numbers_ok or not 1 <= pair[0] <= pair[1] <= line_count:
            raise CaseError(f"{where}: line range {pair!r} is not a [first, last] pair inside the "
                            f"module's {line_count} lines")
        ranges.append((pair[0], pair[1]))
    return tuple(ranges)


def _keywords(raw: Any, where: str, statement: str) -> Tuple[str, ...]:
    if not isinstance(raw, list) or not MIN_KEYWORDS <= len(raw) <= MAX_KEYWORDS:
        raise CaseError(f"{where}: give {MIN_KEYWORDS} to {MAX_KEYWORDS} keyword alternatives")
    for pattern in raw:
        if not isinstance(pattern, str) or not pattern or pattern != pattern.lower():
            raise CaseError(f"{where}: keyword {pattern!r} must be non-empty lower-case text")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise CaseError(f"{where}: keyword {pattern!r} is not a valid pattern ({exc})") from exc
    if not any(re.search(pattern, statement.lower()) for pattern in raw):
        raise CaseError(f"{where}: no keyword matches the key's own statement, so the keywords and the "
                        "statement disagree about what the defect is")
    return tuple(raw)


def _answer_key(raw: Any, where: str, line_count: int) -> AnswerKey:
    if not isinstance(raw, dict):
        raise CaseError(f"{where}: \"key\" must be an object, or null for a clean control")
    defect_class, statement = raw.get("class"), raw.get("statement")
    if not isinstance(defect_class, str) or not defect_class.strip():
        raise CaseError(f"{where}: the key needs a \"class\"")
    if not isinstance(statement, str) or not statement.strip():
        raise CaseError(f"{where}: the key needs a one-sentence \"statement\"")
    return AnswerKey(defect_class.strip(), _line_ranges(raw.get("lines"), where, line_count),
                     statement.strip(), _keywords(raw.get("keywords"), where, statement))


def _check_module(module: str, where: str) -> None:
    count = len(module.splitlines())
    if not MIN_MODULE_LINES <= count <= MAX_MODULE_LINES:
        raise CaseError(f"{where}: the module has {count} lines; keep it between {MIN_MODULE_LINES} "
                        f"and {MAX_MODULE_LINES}")
    hint = HINT_WORDS.search(module)
    if hint:
        raise CaseError(f"{where}: the module contains the word {hint.group(0)!r}, which could point a "
                        "reviewer at the defect")


def load_case(root: Path) -> Case:
    root = root.resolve()
    for needed in (MODULE_FILE, CASE_FILE, TEST_FILE):
        if not (root / needed).is_file():
            raise CaseError(f"{root}: missing {needed}")
    module = (root / MODULE_FILE).read_text(encoding="utf-8")
    _check_module(module, str(root / MODULE_FILE))
    config = _read_json(root / CASE_FILE)
    description = config.get("description")
    if not isinstance(description, str) or not description.strip():
        raise CaseError(f"{root / CASE_FILE}: \"description\" must say what the module is meant to do")
    if "key" not in config:
        raise CaseError(f"{root / CASE_FILE}: give a \"key\", or null for a clean control")
    key = None if config["key"] is None else _answer_key(config["key"], str(root / CASE_FILE),
                                                         len(module.splitlines()))
    has_fix = (root / FIXED_FILE).is_file()
    if key is not None and not has_fix:
        raise CaseError(f"{root}: a seeded case needs {FIXED_FILE}")
    if key is None and has_fix:
        raise CaseError(f"{root}: a control has no defect, so it must not have a {FIXED_FILE}")
    return Case(root.name, root, description.strip(), module, key)


def discover(cases_dir: Path, names: Optional[Sequence[str]] = None) -> List[Case]:
    """Every case under `cases_dir`, or the named ones, in name order."""
    if not cases_dir.is_dir():
        raise CaseError(f"no such cases directory: {cases_dir}")
    found = {path.name: path for path in sorted(cases_dir.iterdir())
             if path.is_dir() and (path / CASE_FILE).is_file()}
    if names is None:
        return [load_case(path) for path in found.values()]
    unknown = [name for name in names if name not in found]
    if unknown:
        raise CaseError(f"unknown case(s): {', '.join(unknown)}; available: {', '.join(found)}")
    return [load_case(found[name]) for name in dict.fromkeys(names)]
