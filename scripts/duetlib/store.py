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
HOME_ENV = "DUET_HOME"
GUARD_ENV = "DUET_GUARD_DIR"
PRIVATE_DIR_MODE = 0o700
STORE_HOME = "home"
STORE_WORKSPACE = "workspace"
SEALED_FILES = (STATE_FILE, MESSAGES_FILE)
FALLBACK_NOTICE = (
    "the duet state directory {home} is not writable from here, so this run is stored inside the "
    "workspace ({root}/), where the peer can read and edit it: isolation is weaker and edits are only "
    "detected, not prevented. For the stronger mode start Codex with --add-dir {home}"
)


class DuetError(Exception):
    """A problem the caller should see as a plain message."""


class TamperError(DuetError):
    """The run's record does not match its integrity record."""


def duet_home() -> Path:
    return Path(os.environ.get(HOME_ENV) or DEFAULT_HOME).expanduser()


def runs_home() -> Path:
    return duet_home() / "runs"


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
    if _writable(runs_home()):
        return runs_home(), STORE_HOME
    return workspace / RUN_ROOT, STORE_WORKSPACE


def is_guarded(run: Path) -> bool:
    """True for a run kept anywhere but the state directory: the peer can reach its files."""
    return runs_home().resolve() not in run.resolve().parents


def hidden_from_peer(run: Path) -> Path:
    """The folder the peer has no business reading: the whole state directory, or the in-workspace store."""
    return run.resolve().parent if is_guarded(run) else duet_home().resolve()


def _guard_root() -> Path:
    custom = os.environ.get(GUARD_ENV)
    return Path(custom) if custom else Path(tempfile.gettempdir()) / f"duet-guard-{os.getuid()}"


def seal_path(run: Path) -> Path:
    """The integrity record sits outside the workspace, named from the run's own location."""
    where = hashlib.sha256(str(run.resolve()).encode("utf-8", "replace")).hexdigest()[:12]
    return _guard_root() / f"{run.name}-{where}.json"


def _digests(run: Path) -> Dict[str, Optional[str]]:
    found: Dict[str, Optional[str]] = {}
    for name in SEALED_FILES:
        path = run / name
        found[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    return found


def reseal(run: Path) -> None:
    """Record what state.json and messages.jsonl hold now. Call with the run's lock held."""
    if not is_guarded(run):
        return
    path = seal_path(run)
    path.parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    handle, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".")
    with os.fdopen(handle, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(_digests(run), sort_keys=True) + "\n")
    os.replace(tmp, path)


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
    current = _digests(run)
    return [name for name in SEALED_FILES if not isinstance(recorded, dict) or recorded.get(name) != current[name]]


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
                              f"and messages cannot be trusted. {fix}")
        reseal(run)
        return "this run predates the integrity record; it is trusted as it stands from now on"
    if changed:
        raise TamperError(f"{' and '.join(changed)} changed outside the runner since its last command, so "
                          f"approvals and check results in this run cannot be trusted. {fix}")
    return None
