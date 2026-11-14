"""Copy of the workspace's source files taken before a read-only peer turn, so a change can be undone."""
from __future__ import annotations

import ctypes
import difflib
import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Dict, List, Optional

from . import gates

DEFAULT_MAX_MB = 1024
MAX_ENV = "DUET_SNAPSHOT_MAX_MB"
PATCH_MAX_BYTES = 1024 * 1024
CLONE_NOFOLLOW = 1
MISSING = gates.MISSING  # the manifest's value for a listed file that is not on disk
DIR_SUFFIX = gates.DIR_SUFFIX  # the manifest lists a folder under its name with this suffix
KEPT_NAME = "snapshot-kept"  # where the copy stays when the workspace could not be put back exactly
STILL_DIFFERS = "(workspace still differs)"

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


_LIBC: List[object] = []  # the C library handle, loaded once: a large workspace clones tens of thousands of files


def _clonefile(source: Path, target: Path) -> bool:
    """APFS copy-on-write clone: instant and takes no extra space. False when it is not available."""
    if sys.platform != "darwin":
        return False
    try:
        if not _LIBC:
            _LIBC.append(ctypes.CDLL(None, use_errno=True))
        return _LIBC[0].clonefile(os.fsencode(source), os.fsencode(target), CLONE_NOFOLLOW) == 0  # type: ignore[attr-defined]
    except (OSError, AttributeError):
        return False


def copy_entry(source: Path, target: Path, made: Optional[set] = None) -> None:
    """Copy one file or symbolic link, keeping its mode and modification time. `made` remembers the
    folders already created during one bulk copy."""
    parent = target.parent
    if made is None or parent not in made:
        parent.mkdir(parents=True, exist_ok=True)
        if made is not None:
            made.add(parent)
    if source.is_symlink():
        os.symlink(os.readlink(source), target)
    elif not _clonefile(source, target):
        shutil.copy2(str(source), str(target), follow_symlinks=False)


def take(workspace: Path, files: Manifest, dest: Path) -> bool:
    """Copy every listed file into dest. False (and nothing copied) when the workspace is over the ceiling."""
    names = [name for name, digest in files.items() if digest != MISSING]
    if total_size(workspace, names) > max_bytes() or max_bytes() == 0:
        return False
    made: set = set()
    for name in names:
        try:
            if name.endswith(DIR_SUFFIX):
                (dest / name).mkdir(parents=True, exist_ok=True)
            else:
                copy_entry(workspace / name, dest / name, made)
        except OSError:
            shutil.rmtree(dest, ignore_errors=True)
            return False
    dest.mkdir(parents=True, exist_ok=True)
    # Apply directory modes last: a source directory may itself be read-only.
    try:
        for name in reversed(sorted(name for name in names if name.endswith(DIR_SUFFIX))):
            shutil.copystat(workspace / name, dest / name, follow_symlinks=False)
    except OSError:
        discard(dest)
        return False
    return True


def discard(dest: Optional[Path]) -> None:
    if dest is not None and not dest.is_symlink():
        # Snapshots preserve read-only directory modes. Make only their real directories writable
        # before removal, leaving symbolic links and everything behind them untouched.
        for root, folders, _ in os.walk(dest, followlinks=False):
            for folder in [Path(root)] + [Path(root) / name for name in folders]:
                try:
                    if not folder.is_symlink():
                        folder.chmod(folder.stat().st_mode | stat.S_IRWXU)
                except OSError:
                    pass
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
        try:
            if _link_above(workspace, current):
                continue
            if _present(current) and (current.is_symlink() or not current.is_dir()):
                copy_entry(current, dest / "files" / name)
        except OSError:
            pass  # unreadable, or behind a folder that cannot be searched: the caller sees the copy is missing
        old = _lines(snap / name) if snap is not None else None
        new = _lines(current)
        if old is not None and new is not None:
            patch += list(difflib.unified_diff(old, new, f"a/{name}", f"b/{name}"))
    if patch:
        (dest / "changes.patch").write_text("".join(line if line.endswith("\n") else line + "\n" for line in patch),
                                            encoding="utf-8")
    return listing


def _link_above(workspace: Path, target: Path) -> bool:
    """True when a folder between the workspace and the target is a symbolic link. Whatever sits behind
    such a link is not the workspace's: it is never read as evidence, removed or written."""
    folder = workspace
    for part in target.relative_to(workspace).parts[:-1]:
        folder = folder / part
        if os.path.islink(folder):  # False, without raising, behind a folder that cannot be searched
            return True
    return False


def _prune_empty_parents(workspace: Path, path: Path, before: Manifest) -> None:
    """Drop folders left empty by removing something the peer created; stops at the first that is not empty."""
    folder = path.parent
    while folder != workspace and workspace in folder.parents:
        if before.get(folder.relative_to(workspace).as_posix() + DIR_SUFFIX, MISSING) != MISSING:
            return
        try:
            folder.rmdir()
        except OSError:
            return
        folder = folder.parent


def preserve_folder(source: Path, dest: Path) -> bool:
    """Move a whole folder aside (copying when it sits on another volume). False when it could not be kept
    completely; nothing is removed from the source in that case."""
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if os.path.lexists(dest):
            return False
        try:
            os.rename(source, dest)
            return True
        except OSError:
            shutil.copytree(str(source), str(dest), symlinks=True)
            return True
    except (OSError, shutil.Error):
        shutil.rmtree(dest, ignore_errors=True)
        return False


def reopen(workspace: Path, snap: Path, before: Manifest, after: Manifest) -> List[str]:
    """Make folders that can no longer be read or searched usable again, parents first, so that what is
    inside them can be compared and restored: a folder from before the turn gets its old permissions
    back, one created during the turn gets its owner's access. Returns the folders reopened."""
    known = [name for name in before if before[name] != MISSING]
    reopened: List[str] = []
    for name in gates.closed_folders(workspace, sorted(set(known) | set(after))):
        target = workspace / name
        try:
            if _link_above(workspace, target):
                continue
            if before.get(name, MISSING) != MISSING:
                target.chmod(stat.S_IMODE((snap / name).lstat().st_mode))
            else:
                target.chmod(stat.S_IMODE(target.lstat().st_mode) | stat.S_IRWXU)
        except OSError:
            continue
        if os.access(target, os.R_OK | os.X_OK):
            reopened.append(name)
    return reopened


def _clear(workspace: Path, changed: List[str], before: Manifest, failed: List[str],
           aside: Optional[Path] = None) -> List[Path]:
    """Remove what is there now for every changed name; return the folders the peer created.

    A folder standing where a file or link was is never deleted with its contents: it is moved whole
    into `aside`, and when that cannot be done it stays where it is and the name counts as failed."""
    made: List[Path] = []
    for name in changed:
        target = workspace / name
        try:
            if _link_above(workspace, target):
                continue  # behind a link: the link is its own entry, and what it points at is left alone
            if name.endswith(DIR_SUFFIX):
                if before.get(name, MISSING) == MISSING:
                    made.append(target)
                continue
            if target.is_dir() and not target.is_symlink():
                if aside is None or not preserve_folder(target, aside / name):
                    failed.append(name)
                    continue
            _remove(target)
            if before.get(name, MISSING) == MISSING:
                _prune_empty_parents(workspace, target, before)
        except OSError:
            failed.append(name)
    return made


def _put_back(workspace: Path, snap: Path, changed: List[str], before: Manifest, failed: List[str]) -> None:
    for name in changed:
        if before.get(name, MISSING) == MISSING or name in failed:
            continue
        target = workspace / name
        try:
            if _link_above(workspace, target):
                raise OSError("a folder on the way is a symbolic link")
            if name.endswith(DIR_SUFFIX):
                if target.is_symlink():
                    raise OSError("the original folder is now a symbolic link")
                target.mkdir(parents=True, exist_ok=True)
            else:
                copy_entry(snap / name, target)
        except OSError:
            failed.append(name)
    for name in reversed(changed):
        if name.endswith(DIR_SUFFIX) and before.get(name, MISSING) != MISSING and name not in failed:
            try:
                shutil.copystat(snap / name, workspace / name, follow_symlinks=False)
            except OSError:
                failed.append(name)


def restore(workspace: Path, snap: Path, changed: List[str], before: Manifest,
            aside: Optional[Path] = None) -> List[str]:
    """Put each changed file, link and folder back as it was, never writing through a symbolic
    link. Returns the names that could not be restored (plus STILL_DIFFERS when the workspace does not
    match afterwards for another reason); in that case the snapshot is kept beside the turn as KEPT_NAME.
    `aside` is where a folder that replaced a file is moved, contents and all, before the file returns."""
    failed: List[str] = []
    ordered = sorted(changed)
    for folder in reversed(_clear(workspace, ordered, before, failed, aside)):
        try:
            folder.rmdir()
            _prune_empty_parents(workspace, folder, before)
        except OSError:
            pass  # it holds ignored files or caches; the comparison below decides
    _put_back(workspace, snap, ordered, before, failed)
    if not failed and gates.manifest(workspace) != before:
        failed.append(STILL_DIFFERS)
    if failed:
        keep(snap)
    return failed


def keep(snap: Path) -> Optional[Path]:
    """Move a snapshot out of the way of `discard`, so the originals survive a failed restore."""
    kept = snap.parent / KEPT_NAME
    if not snap.is_dir():
        return kept if kept.is_dir() else None
    try:
        if kept.exists():
            discard(kept)
        snap.rename(kept)
    except OSError:
        return None
    return kept
