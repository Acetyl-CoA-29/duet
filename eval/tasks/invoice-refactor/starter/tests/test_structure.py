import ast
from pathlib import Path

MAX_LINES = 20
MIN_FUNCTIONS = 5


def _functions():
    tree = ast.parse(Path(__file__).resolve().parents[1].joinpath("invoice.py").read_text(encoding="utf-8"))
    return [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]


def test_functions_are_short():
    too_long = {f.name: f.end_lineno - f.lineno + 1 for f in _functions() if f.end_lineno - f.lineno + 1 > MAX_LINES}
    assert not too_long, f"functions longer than {MAX_LINES} lines: {too_long}"


def test_module_is_split_into_functions():
    assert len(_functions()) >= MIN_FUNCTIONS


def test_entry_point_is_kept():
    assert "render_invoice" in {f.name for f in _functions()}
