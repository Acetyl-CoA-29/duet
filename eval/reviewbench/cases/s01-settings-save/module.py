"""Load and save application settings as JSON."""
import json
import os
from pathlib import Path
from typing import Any, Dict

DEFAULTS: Dict[str, Any] = {"theme": "light", "autosave": True, "recent_limit": 10}
ALLOWED_THEMES = ("light", "dark")


class SettingsError(Exception):
    """The settings cannot be used."""


def validate(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Return a checked copy of `settings`, with defaults filled in."""
    merged = {**DEFAULTS, **settings}
    if merged["theme"] not in ALLOWED_THEMES:
        raise SettingsError(f"unknown theme: {merged['theme']!r}")
    if not isinstance(merged["autosave"], bool):
        raise SettingsError("autosave must be true or false")
    limit = merged["recent_limit"]
    if isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= 100:
        raise SettingsError("recent_limit must be a whole number from 0 to 100")
    return merged


def load_settings(path: Path) -> Dict[str, Any]:
    """Read settings from `path`. A missing file means the defaults."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return dict(DEFAULTS)
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SettingsError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise SettingsError(f"{path} must hold a JSON object")
    return validate(raw)


def save_settings(path: Path, settings: Dict[str, Any]) -> bool:
    """Write `settings` to `path` through a temporary file, so a reader never sees half a file.
    Returns True once the new file is in place and False when it could not be written."""
    payload = json.dumps(validate(settings), indent=2, sort_keys=True) + "\n"
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.replace(str(temporary), str(path))
    except OSError:
        pass
    return True
