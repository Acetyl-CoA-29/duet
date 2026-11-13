"""Copy of the workspace's source files taken before a read-only peer turn, so a change can be undone."""
from __future__ import annotations

import ctypes
import difflib
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional

DEFAULT_MAX_MB = 1024
MAX_ENV = "DUET_SNAPSHOT_MAX_MB"
PATCH_MAX_BYTES = 1024 * 1024
CLONE_NOFOLLOW = 1
MISSING = "unreadable-or-deleted"  # the manifest's value for a listed file that is not on disk

Manifest = Dict[str, str]


def max_bytes() -> int:
    """Largest workspace, in bytes, that is copied before a read-only turn (0 switches the copy off)."""
    try:
        return max(0, int(os.environ.get(MAX_ENV, DEFAULT_MAX_MB))) * 1024 * 1024
    except ValueError:
        return DEFAULT_MAX_MB * 1024 * 1024


def total_size(workspace: Path, names: List[str]) -> int:
    total = 0
    for name in names:
        try:
            total += (workspace / name).lstat().st_size
        except OSError:
            continue
    return total


def _clonefile(source: Path, target: Path) -> bool:
    """APFS copy-on-write clone: instant and takes no extra space. False when it is not available."""
    if sys.platform != "darwin":
        return False
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        return libc.clonefile(os.fsencode(source), os.fsencode(target), CLONE_NOFOLLOW) == 0
    except (OSError, AttributeError):
        return False


def copy_entry(source: Path, target: Path) -> None:
    """Copy one file or symbolic link, keeping its mode and modification time."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        os.symlink(os.readlink(source), target)
    elif not _clonefile(source, target):
        shutil.copy2(str(source), str(target), follow_symlinks=False)


def take(workspace: Path, files: Manifest, dest: Path) -> bool:
    """Copy every listed file into dest. False (and nothing copied) when the workspace is over the ceiling."""
    names = [name for name, digest in files.items() if digest != MISSING]
    if total_size(workspace, names) > max_bytes() or max_bytes() == 0:
        return False
    for name in names:
        try:
            copy_entry(workspace / name, dest / name)
        except OSError:
            shutil.rmtree(dest, ignore_errors=True)
            return False
    dest.mkdir(parents=True, exist_ok=True)
    return True


def discard(dest: Optional[Path]) -> None:
    if dest is not None:
        shutil.rmtree(dest, ignore_errors=True)


def _present(path: Path) -> bool:
    return path.is_symlink() or path.exists()


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif _present(path):
        path.unlink()


def _kind(name: str, before: Manifest, after: Manifest) -> str:
    if before.get(name, MISSING) == MISSING:
        return "created"
    return "deleted" if after.get(name, MISSING) == MISSING else "modified"


def _lines(path: Path) -> Optional[List[str]]:
    """A file's lines when it is small text; None for anything a patch cannot show."""
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > PATCH_MAX_BYTES:
            return [] if not _present(path) else None
        return path.read_text(encoding="utf-8").splitlines(keepends=True)
    except (OSError, UnicodeDecodeError):
        return None


def save_evidence(workspace: Path, snap: Optional[Path], changed: List[str], before: Manifest,
                  after: Manifest, dest: Path) -> List[str]:
    """Keep what the peer did: a listing, the peer's version of each file, and a patch for text files."""
    dest.mkdir(parents=True, exist_ok=True)
    listing = [f"{_kind(name, before, after)} {name}" for name in changed]
    (dest / "changes.txt").write_text("\n".join(listing) + "\n", encoding="utf-8")
    patch: List[str] = []
    for name in changed:
        current = workspace / name
        if _present(current) and not current.is_dir():
            try:
                copy_entry(current, dest / "files" / name)
            except OSError:
                pass
        old = _lines(snap / name) if snap is not None else None
        new = _lines(current)
        if old is not None and new is not None:
            patch += list(difflib.unified_diff(old, new, f"a/{name}", f"b/{name}"))
    if patch:
        (dest / "changes.patch").write_text("".join(line if line.endswith("\n") else line + "\n" for line in patch),
                                            encoding="utf-8")
    return listing


def _prune_empty_parents(workspace: Path, path: Path) -> None:
    """Drop folders left empty by removing a file the peer created; stops at the first that is not empty."""
    folder = path.parent
    while folder != workspace and workspace in folder.parents:
        try:
            folder.rmdir()
        except OSError:
            return
        folder = folder.parent


def restore(workspace: Path, snap: Path, changed: List[str], before: Manifest) -> List[str]:
    """Put each changed file back as it was. Returns the names that could not be restored."""
    failed: List[str] = []
    for name in changed:
        target = workspace / name
        try:
            _remove(target)
            if before.get(name, MISSING) == MISSING:
                _prune_empty_parents(workspace, target)
            else:
                copy_entry(snap / name, target)
        except OSError:
            failed.append(name)
    return failed
