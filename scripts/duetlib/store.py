"""Where a run is kept, and the integrity record required for every run."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

RUN_ROOT = ".duet"
STATE_FILE = "state.json"
MESSAGES_FILE = "messages.jsonl"
DEFAULT_HOME = "~/.local/state/duet"
DEFAULT_GUARD = "~/.local/state/duet-guard"
RUNS_DIR = "runs"
PENDING_KEY = "pending"
HOME_ENV = "DUET_HOME"
GUARD_ENV = "DUET_GUARD_DIR"
PRIVATE_DIR_MODE = 0o700
STORE_HOME = "home"
STORE_WORKSPACE = "workspace"
SEALED_FILES = (STATE_FILE, MESSAGES_FILE)
FALLBACK_NOTICE = (
    "the duet state directory {home} is not writable from here, so this run is stored inside the "
    "workspace ({root}/), where the peer can read and edit it: isolation is weaker and edits are only "
    "detected, not prevented. For the stronger mode make {home} writable for this command (a Codex host: start Codex with "
    "--add-dir {home})"
)


class DuetError(Exception):
    """A problem the caller should see as a plain message."""


class TamperError(DuetError):
    """The run's record does not match its integrity record."""


def duet_home() -> Path:
    return Path(os.environ.get(HOME_ENV) or DEFAULT_HOME).expanduser()


def runs_home() -> Path:
    return duet_home() / RUNS_DIR


def _writable(folder: Path) -> bool:
    """Create the folder if needed and prove a file can be written there (a sandbox may forbid it)."""
    try:
        folder.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
        handle, probe = tempfile.mkstemp(dir=str(folder), prefix=".probe-")
        os.close(handle)
        os.unlink(probe)
    except OSError:
        return False
    return True


def choose(workspace: Path) -> Tuple[Path, str]:
    """The folder new runs go in: outside the workspace when possible, else inside it."""
    home, inside = duet_home().resolve(), workspace.resolve()
    root = runs_home().resolve()
    if home == inside or inside in home.parents or root == inside or inside in root.parents:
        raise DuetError(f"the duet state directory ({root}, from {HOME_ENV}) is inside the workspace: the "
                        "record would be part of the files being judged and within the peer's reach. Point "
                        f"{HOME_ENV} somewhere outside {inside}, or unset it")
    custom_guard = os.environ.get(GUARD_ENV)
    if custom_guard:
        guard = Path(custom_guard).expanduser().resolve()
        if any(guard == folder or folder in guard.parents for folder in (inside, home, root)):
            raise DuetError(f"{GUARD_ENV} ({guard}) must be outside the workspace and the duet state directory")
    if _writable(runs_home()):
        return runs_home(), STORE_HOME
    fallback = workspace / RUN_ROOT
    if fallback.is_symlink():
        raise DuetError(f"the fallback run store ({fallback}) is a symbolic link; use a real .duet directory "
                        "so its location and isolation cannot change through that link")
    return fallback, STORE_WORKSPACE


def hidden_from_peer(run: Path) -> Path:
    """The folder the peer has no business reading: the whole state directory, or the in-workspace store."""
    where = run.resolve()
    in_state_directory = where.parent.name == RUNS_DIR and RUN_ROOT not in where.parts
    return where.parent.parent if in_state_directory else where.parent


def _guard_roots() -> List[Path]:
    """Where integrity records may live, best first: DUET_GUARD_DIR alone when it is set; otherwise a
    folder beside the state directory (it survives a reboot) and then the temp directory. Writability
    by a peer depends on its inherited permissions; the seal detects edits rather than isolating them."""
    custom = os.environ.get(GUARD_ENV)
    if custom:
        return [Path(custom).expanduser()]
    return [Path(DEFAULT_GUARD).expanduser(), Path(tempfile.gettempdir()) / f"duet-guard-{os.getuid()}"]


def _seal_name(run: Path) -> str:
    resolved = run.resolve()
    where = hashlib.sha256(str(resolved).encode("utf-8", "replace")).hexdigest()[:12]
    return f"{resolved.name}-{where}.json"


def _mtime(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return -1


def seal_path(run: Path) -> Path:
    """The run's integrity record: the newest one found, or where the next one would be written. It sits
    outside the workspace and is named from the run's own location."""
    candidates = [root / _seal_name(run) for root in _guard_roots()]
    found = [path for path in candidates if path.is_file()]
    return max(found, key=_mtime) if found else candidates[0]


def _digests(run: Path) -> Dict[str, Optional[str]]:
    found: Dict[str, Optional[str]] = {}
    for name in SEALED_FILES:
        path = run / name
        found[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    return found


def _write_seal(path: Path, text: str) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
        handle, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".")
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except OSError:
        return False
    return True


def reseal(run: Path, pending: Optional[Dict[str, str]] = None) -> None:
    """Record what state.json and messages.jsonl hold now. Call with the run's lock held.

    `pending` names the digest a file is about to have: the record then accepts either, so a runner
    killed between writing the file and sealing it again does not look like tampering."""
    record: Dict[str, object] = dict(_digests(run))
    if pending:
        record[PENDING_KEY] = dict(pending)
    text = json.dumps(record, sort_keys=True) + "\n"
    candidates = [root / _seal_name(run) for root in _guard_roots()]
    written = next((path for path in candidates if _write_seal(path, text)), None)
    if written is None:
        raise DuetError("the integrity record for this run could not be written to any of: "
                        + ", ".join(str(path.parent) for path in candidates)
                        + f". Set {GUARD_ENV} to a writable folder outside the workspace")
    for stale in candidates:
        if stale != written:
            try:
                stale.unlink()
            except OSError:
                pass


def changed_since_seal(run: Path) -> Optional[List[str]]:
    """Names of sealed files that differ from the record; None when there is no record to compare with."""
    path = seal_path(run)
    if not path.is_file():
        return None
    try:
        recorded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return list(SEALED_FILES)
    if not isinstance(recorded, dict):
        return list(SEALED_FILES)
    current = _digests(run)
    pending = recorded.get(PENDING_KEY) if isinstance(recorded.get(PENDING_KEY), dict) else {}
    return [name for name in SEALED_FILES
            if current[name] != recorded.get(name) and (current[name] is None or current[name] != pending.get(name))]


def verify(run: Path) -> Optional[str]:
    """Raise TamperError if the record changed or its seal is missing, including for legacy runs."""
    changed = changed_since_seal(run)
    fix = (f"Read the record before trusting it. If the user accepts it as it stands: "
           f"`reseal --run {run} --by-user \"<their words>\"`")
    if changed is None:
        raise TamperError(f"the integrity record for this run is missing ({seal_path(run)}), so its state "
                          "and messages cannot be trusted. Legacy unsealed runs also need explicit resealing. "
                          "The seal can be lost to a cleared temp directory, a different "
                          f"{GUARD_ENV}, or deletion. {fix}")
    if changed:
        raise TamperError(f"{' and '.join(changed)} changed outside the runner since its last command, so "
                          f"approvals and check results in this run cannot be trusted. {fix}")
    return None
