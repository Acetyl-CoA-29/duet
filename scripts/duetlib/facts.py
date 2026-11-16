"""Facts the runner computes about the change itself, so "no slop" is checked and not only promised.

At init the runner records, per source file, a few counts that are cheap to compute and hard to
argue with: unfinished-work markers, functions with no body, errors caught and silently dropped,
tests and assertions, skipped tests, and strings shaped like credentials. Later it computes the same
counts for the files that changed and reports what got worse. Those findings are shown to the
reviewer, listed by `status`, and block `finish` until each is fixed or justified on the record.

These are heuristics. They cannot show that code is good; they can show that a stub, a dropped
error, a weakened test or a pasted key did not slip through unnoticed. Counts are compared per file,
so moving flagged code between files reports it again: that is deliberate.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

from . import gates

BASELINE_FILE = "baseline-facts.json"
MAX_FILE_BYTES = 512 * 1024
CODE_SUFFIXES = frozenset((
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs", ".java", ".kt", ".swift", ".rb",
    ".php", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".scala", ".sh", ".bash", ".zsh", ".sql",
))
SECRET_SUFFIXES = CODE_SUFFIXES | frozenset((
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".env", ".txt", ".md", ".tf", ".properties", ""))

STUB_RE = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b|NotImplementedError|\bunimplemented!\s*\(|\btodo!\s*\("
                     r"|not\s+(yet\s+)?implemented", re.IGNORECASE)
EMPTY_CATCH_RE = re.compile(r"\bcatch\s*(\([^)]*\))?\s*\{\s*\}|\brescue\b[^\n]*\n\s*end\b|except[^\n:]*:\s*pass\b")
SKIP_RE = re.compile(r"@(pytest\.mark\.)?(skip|skipif|xfail)\b|@unittest\.skip|\bpytest\.skip\s*\("
                     r"|\b(it|test|describe)\.(skip|only|todo)\s*\(|\bx(it|describe|test)\s*\(|\bt\.Skip(f|Now)?\s*\("
                     r"|#\[ignore\]|@Disabled\b|@Ignore\b")
OTHER_TEST_RE = re.compile(r"^\s*(?:it|test)\s*\(\s*(['\"`])(.+?)\1|^\s*func\s+(Test\w+)\s*\(|^\s*fn\s+(test\w*)\s*\(",
                           re.MULTILINE)
OTHER_ASSERT_RE = re.compile(r"\bexpect\s*\(|\bassert\w*\s*[(!]|\bt\.(Error|Fatal)f?\s*\(|\brequire\.\w+\s*\(")
TEST_NAME_RE = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*$|_test\.[a-z]+$|\.(test|spec)\.[a-z]+$")
SECRET_RE = re.compile(
    r"AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|\bsk-[A-Za-z0-9_-]{24,}|\bghp_[A-Za-z0-9]{36}\b"
    r"|\bgithub_pat_[A-Za-z0-9_]{40,}|\bxox[baprs]-[A-Za-z0-9-]{12,}|\bAIza[0-9A-Za-z_-]{35}\b")
SILENT_NAMES = frozenset(("Exception", "BaseException"))

Facts = Dict[str, Any]
Finding = Dict[str, Any]


def is_test_file(name: str) -> bool:
    return bool(TEST_NAME_RE.search(name))


def _only_placeholder(body: List[ast.stmt]) -> bool:
    """True when a body does nothing: only `pass`, `...`, or a docstring."""
    def inert(node: ast.stmt) -> bool:
        if isinstance(node, ast.Pass):
            return True
        return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    return all(inert(node) for node in body)


def _is_declared_abstract(node: ast.AST) -> bool:
    names = {getattr(d, "attr", getattr(d, "id", "")) for d in getattr(node, "decorator_list", [])}
    return bool(names & {"abstractmethod", "overload", "abstractproperty"})


def _drops_the_error(handler: ast.ExceptHandler) -> bool:
    """A bare or catch-all handler whose body neither raises, logs, nor does anything else."""
    kind = handler.type
    broad = kind is None or (isinstance(kind, ast.Name) and kind.id in SILENT_NAMES)
    if not broad:
        return False
    def quiet(node: ast.stmt) -> bool:
        if isinstance(node, (ast.Pass, ast.Continue, ast.Break)):
            return True
        if isinstance(node, ast.Return):
            return node.value is None or isinstance(node.value, ast.Constant)
        return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    return all(quiet(node) for node in handler.body)


def _python_facts(text: str, in_protocol_file: bool) -> Optional[Facts]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None
    empty: List[str] = []
    tests: List[str] = []
    silent = asserts = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test"):
                tests.append(node.name)
            if _only_placeholder(node.body) and not _is_declared_abstract(node) and not in_protocol_file:
                empty.append(node.name)
        elif isinstance(node, ast.ExceptHandler) and _drops_the_error(node):
            silent += 1
        elif isinstance(node, ast.Assert):
            asserts += 1
        elif isinstance(node, ast.Call):
            called = getattr(node.func, "attr", getattr(node.func, "id", ""))
            if called.startswith("assert") or called == "raises":
                asserts += 1
    return {"empty": sorted(empty), "silent": silent, "tests": sorted(tests), "asserts": asserts}


def file_facts(name: str, text: str) -> Facts:
    """The counts for one file's text. Python is parsed; other languages fall back to patterns."""
    suffix = Path(name).suffix.lower()
    code = suffix in CODE_SUFFIXES
    parsed = _python_facts(text, suffix == ".pyi") if suffix in (".py", ".pyi") else None
    if parsed is None:
        found = [next(part for part in match.groups()[1:] if part) for match in OTHER_TEST_RE.finditer(text)]
        parsed = {"empty": [], "silent": len(EMPTY_CATCH_RE.findall(text)) if code else 0,
                  "tests": sorted(found) if code else [],
                  "asserts": len(OTHER_ASSERT_RE.findall(text)) if code else 0}
    return {
        **parsed,
        "stubs": len(STUB_RE.findall(text)) if code else 0,
        "skips": len(SKIP_RE.findall(text)) if code else 0,
        "secrets": len(SECRET_RE.findall(text)) if suffix in SECRET_SUFFIXES else 0,
        "test_file": is_test_file(name),
    }


def _read(workspace: Path, name: str) -> Optional[str]:
    path = workspace / name
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data:
        return None
    return data.decode("utf-8", "replace")


def _watched(name: str) -> bool:
    return not name.endswith(gates.DIR_SUFFIX) and Path(name).suffix.lower() in SECRET_SUFFIXES


def scan(workspace: Path, names: Iterable[str]) -> Dict[str, Facts]:
    found: Dict[str, Facts] = {}
    for name in names:
        text = _read(workspace, name) if _watched(name) else None
        if text is not None:
            found[name] = file_facts(name, text)
    return found


def record_baseline(run: Path, workspace: Path) -> str:
    """Save the starting facts in the run folder and return the digest to keep in the sealed state."""
    files = gates.manifest(workspace)
    payload = json.dumps({"files": files, "facts": scan(workspace, files)}, sort_keys=True)
    (run / BASELINE_FILE).write_text(payload, encoding="utf-8")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _baseline(run: Path, digest: Optional[str]) -> Optional[Dict[str, Any]]:
    """The recorded baseline, or None when the run has none or the file no longer matches the state."""
    if not digest:
        return None
    try:
        payload = (run / BASELINE_FILE).read_text(encoding="utf-8")
    except OSError:
        return None
    if hashlib.sha256(payload.encode("utf-8")).hexdigest() != digest:
        return None
    try:
        data = json.loads(payload)
    except ValueError:
        return None
    return data if isinstance(data, dict) and isinstance(data.get("files"), dict) else None


def _finding(kind: str, name: str, detail: str) -> Finding:
    return {"id": f"{kind}:{name}", "kind": kind, "file": name, "detail": detail}


def _rose(kind: str, name: str, before: int, after: int, what: str) -> List[Finding]:
    return [_finding(kind, name, f"{what}: {before} before, {after} now")] if after > before else []


def _file_findings(name: str, old: Facts, new: Facts) -> List[Finding]:
    found = _rose("stub", name, old.get("stubs", 0), new["stubs"], "unfinished-work markers (TODO, FIXME, not implemented)")
    added: Set[str] = set(new["empty"]) - set(old.get("empty", []))
    if added:
        found.append(_finding("empty", name, "functions with no body: " + ", ".join(sorted(added))))
    found += _rose("silent-catch", name, old.get("silent", 0), new["silent"], "errors caught and silently dropped")
    found += _rose("skipped-test", name, old.get("skips", 0), new["skips"], "skipped, expected-to-fail or focused tests")
    found += _rose("secret", name, old.get("secrets", 0), new["secrets"], "strings shaped like credentials")
    if new["test_file"] or old.get("test_file"):
        gone = set(old.get("tests", [])) - set(new["tests"])
        if gone:
            found.append(_finding("removed-test", name, "tests removed or renamed: " + ", ".join(sorted(gone))))
        if new["asserts"] < old.get("asserts", 0):
            found.append(_finding("fewer-asserts", name,
                                  f"assertions: {old['asserts']} before, {new['asserts']} now"))
    return found


EMPTY_FACTS: Facts = {"empty": [], "silent": 0, "tests": [], "asserts": 0, "stubs": 0, "skips": 0, "secrets": 0,
                      "test_file": False}


def report(run: Path, state: Dict[str, Any], workspace: Path,
           files: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """What changed since the run started and which changes need fixing or a recorded justification.
    `files` is the workspace manifest when the caller has already taken it in this command."""
    empty = {"available": False, "problem": "", "findings": [], "changed_source": [], "changed_tests": [], "notes": []}
    if not state.get("facts_digest"):
        return empty  # a run started before these checks existed
    base = _baseline(run, state.get("facts_digest"))
    if base is None:
        return {**empty, "problem": f"the quality baseline ({BASELINE_FILE} in the run folder) is missing or was "
                                    "changed, so the change cannot be checked for stubs or weakened tests"}
    before_files: Dict[str, str] = base["files"]
    before_facts: Dict[str, Facts] = base.get("facts", {})
    now_files = gates.manifest(workspace) if files is None else files
    changed = sorted(n for n in set(before_files) | set(now_files)
                     if before_files.get(n) != now_files.get(n) and not n.endswith(gates.DIR_SUFFIX))
    now_facts = scan(workspace, [n for n in changed if n in now_files])
    findings: List[Finding] = []
    for name in changed:
        old = before_facts.get(name, EMPTY_FACTS)
        if name not in now_files:
            if old.get("test_file") and old.get("tests"):
                findings.append(_finding("removed-test", name, f"test file deleted ({len(old['tests'])} tests)"))
            continue
        if name in now_facts:
            findings += _file_findings(name, old, now_facts[name])
    tests = [n for n in changed if is_test_file(n)]
    source = [n for n in changed if not is_test_file(n) and Path(n).suffix.lower() in CODE_SUFFIXES]
    notes = ["source files changed but no test file did"] if source and not tests else []
    return {"available": True, "problem": "", "findings": findings, "changed_source": source, "changed_tests": tests, "notes": notes}


def justified(state: Dict[str, Any], finding: Finding) -> bool:
    """A justification covers a finding only while the finding still reads exactly as it did then."""
    return (state.get("justified") or {}).get(finding["id"], {}).get("detail") == finding["detail"]


def open_findings(state: Dict[str, Any], found: Dict[str, Any]) -> List[Finding]:
    return [f for f in found["findings"] if not justified(state, f)]


def blocking(state: Dict[str, Any], found: Dict[str, Any]) -> List[str]:
    lost = [found["problem"] + ". Tell the user: this needs their inspection"] if found.get("problem") else []
    return lost + [f"{f['id']}: {f['detail']}. Fix it, or record why it is right with "
            f"`justify --flag \"{f['id']}\" --reason \"...\"` and have it reviewed"
            for f in open_findings(state, found)]


def for_reviewer(state: Dict[str, Any], found: Dict[str, Any]) -> str:
    """The block shown to the reviewing model. Empty when the run has no baseline."""
    if not found["available"]:
        return found.get("problem", "")
    lines = ["Computed by the runner from the files, comparing now with the start of the run "
             "(heuristics: check each one yourself):"]
    lines.append("- source files changed: " + (", ".join(found["changed_source"]) or "none"))
    lines.append("- test files changed: " + (", ".join(found["changed_tests"]) or "none"))
    lines += [f"- note: {note}" for note in found["notes"]]
    for item in found["findings"]:
        reason = (state.get("justified") or {}).get(item["id"], {}).get("reason") if justified(state, item) else None
        status = f"justified on the record: {reason}" if reason else "NOT justified; finish is blocked"
        lines.append(f"- FLAG {item['id']}: {item['detail']} ({status})")
    if not found["findings"]:
        lines.append("- no flags: no new stubs, dropped errors, weakened or skipped tests, or credential-shaped strings")
    return "\n".join(lines)
