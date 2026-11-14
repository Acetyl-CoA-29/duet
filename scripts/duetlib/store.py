"""Where a run is kept, and the integrity record for runs that have to live inside the workspace."""
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
    if home == inside or inside in home.parents:
        raise DuetError(f"the duet state directory ({home}, from {HOME_ENV}) is inside the workspace: the "
                        "record would be part of the files being judged and within the peer's reach. Point "
                        f"{HOME_ENV} somewhere outside {inside}, or unset it")
    if _writable(runs_home()):
        return runs_home(), STORE_HOME
    return workspace / RUN_ROOT, STORE_WORKSPACE


def is_guarded(run: Path) -> bool:
    """True for a run kept anywhere but a state directory's `runs` folder: the peer can reach its files.
    Decided from where the run sits, never from the current DUET_HOME (which may differ from the one the
    run was started under) and never from state.json (which the peer could edit)."""
    where = run.resolve()
    return where.parent.name != RUNS_DIR or RUN_ROOT in where.parts


def hidden_from_peer(run: Path) -> Path:
    """The folder the peer has no business reading: the whole state directory, or the in-workspace store."""
    return run.resolve().parent if is_guarded(run) else run.resolve().parent.parent


def _guard_roots() -> List[Path]:
    """Where integrity records may live, best first: DUET_GUARD_DIR alone when it is set; otherwise a
    folder beside the state directory (it survives a reboot, and a sandboxed peer cannot write it) and
    then the temp directory (all a sandboxed host can write outside the workspace)."""
    custom = os.environ.get(GUARD_ENV)
    if custom:
        return [Path(custom)]
    return [Path(DEFAULT_GUARD).expanduser(), Path(tempfile.gettempdir()) / f"duet-guard-{os.getuid()}"]


def _seal_name(run: Path) -> str:
    where = hashlib.sha256(str(run.resolve()).encode("utf-8", "replace")).hexdigest()[:12]
    return f"{run.name}-{where}.json"


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
    if not is_guarded(run):
        return
    record: Dict[str, object] = dict(_digests(run))
    if pending:
        record[PENDING_KEY] = dict(pending)
    text = json.dumps(record, sort_keys=True) + "\n"
    candidates = [root / _seal_name(run) for root in _guard_roots()]
    written = next((path for path in candidates if _write_seal(path, text)), None)
    if written is None:
        raise DuetError("the integrity record for this in-workspace run could not be written to any of: "
                        + ", ".join(str(path.parent) for path in candidates)
                        + f". Set {GUARD_ENV} to a writable folder outside the workspace")
    for stale in candidates:
        if stale != written:
            try:
                stale.unlink()
            except OSError:
                pass


def _created_with_a_seal(run: Path) -> bool:
    """Runs from before the integrity record existed carry no `store` field."""
    try:
        return "store" in json.loads((run / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return True


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
    """Raise TamperError if the record was edited outside the runner. Returns a notice for old runs."""
    if not is_guarded(run):
        return None
    changed = changed_since_seal(run)
    fix = (f"Read the record before trusting it. If the user accepts it as it stands: "
           f"`reseal --run {run} --by-user \"<their words>\"`")
    if changed is None:
        if _created_with_a_seal(run):
            raise TamperError(f"the integrity record for this run is missing ({seal_path(run)}), so its state "
                              "and messages cannot be trusted. It is kept outside the workspace and can be lost "
                              f"to a cleared temp directory or a different {GUARD_ENV}; it can also have been "
                              f"deleted. {fix}")
        reseal(run)
        return "this run predates the integrity record; it is trusted as it stands from now on"
    if changed:
        raise TamperError(f"{' and '.join(changed)} changed outside the runner since its last command, so "
                          f"approvals and check results in this run cannot be trusted. {fix}")
    return None
