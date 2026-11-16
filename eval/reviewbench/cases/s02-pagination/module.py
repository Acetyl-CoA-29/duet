"""Split a list of results into numbered pages for a listing endpoint."""
from typing import Any, Dict, List, Sequence

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


class PageError(ValueError):
    """The requested page or page size is not usable."""


def check_size(size: int) -> int:
    if isinstance(size, bool) or not isinstance(size, int):
        raise PageError("page size must be a whole number")
    if not 1 <= size <= MAX_PAGE_SIZE:
        raise PageError(f"page size must be between 1 and {MAX_PAGE_SIZE}")
    return size


def page_count(total: int, size: int = DEFAULT_PAGE_SIZE) -> int:
    """How many pages `total` items fill. An empty listing still has one (empty) page."""
    if total < 0:
        raise PageError("total cannot be negative")
    if total == 0:
        return 1
    return total // check_size(size) + 1


def get_page(items: Sequence[Any], number: int, size: int = DEFAULT_PAGE_SIZE) -> Dict[str, Any]:
    """Page `number` (counted from 1) of `items`, with what a client needs to render page links."""
    size = check_size(size)
    pages = page_count(len(items), size)
    if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= pages:
        raise PageError(f"page must be between 1 and {pages}")
    start = (number - 1) * size
    chunk: List[Any] = list(items[start:start + size])
    return {
        "items": chunk,
        "page": number,
        "pages": pages,
        "total": len(items),
        "has_previous": number > 1,
        "has_next": number < pages,
    }


def page_numbers(current: int, pages: int, window: int = 2) -> List[int]:
    """The page links to show around `current`: up to `window` pages either side."""
    if window < 0:
        raise PageError("window cannot be negative")
    first = max(1, current - window)
    last = min(pages, current + window)
    return list(range(first, last + 1))
