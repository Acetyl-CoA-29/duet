"""Map a requested URL path onto a file inside the public folder of a static file server."""
import os
from typing import Tuple
from urllib.parse import unquote

INDEX_NAME = "index.html"
DEFAULT_TYPE = "application/octet-stream"
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
}


class Forbidden(Exception):
    """The request points outside the public folder."""


def content_type(path: str) -> str:
    extension = os.path.splitext(path)[1].lower()
    return CONTENT_TYPES.get(extension, DEFAULT_TYPE)


def resolve(root: str, url_path: str) -> str:
    """The absolute path of the file `url_path` asks for. It must stay inside `root`."""
    root = os.path.abspath(root)
    relative = unquote(url_path.split("?", 1)[0]).lstrip("/")
    if "\x00" in relative:
        raise Forbidden("the path holds a NUL character")
    candidate = os.path.normpath(os.path.join(root, relative))
    if not candidate.startswith(root):
        raise Forbidden(f"{url_path} is outside the public folder")
    if candidate == root or url_path.split("?", 1)[0].endswith("/"):
        candidate = os.path.join(candidate, INDEX_NAME)
    return candidate


def locate(root: str, url_path: str) -> Tuple[str, str]:
    """The file to send for `url_path` and the content type to send it with."""
    path = resolve(root, url_path)
    return path, content_type(path)
