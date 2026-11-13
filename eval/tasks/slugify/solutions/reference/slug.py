"""URL slugs."""
import re
import unicodedata

FALLBACK = "n-a"


def slugify(text, max_length=None):
    if not isinstance(text, str):
        raise TypeError("text must be a str")
    if max_length is not None and (isinstance(max_length, bool) or not isinstance(max_length, int) or max_length <= 0):
        raise ValueError("max_length must be an int greater than zero")
    text = text.replace("'", "").replace("’", "")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    if max_length is not None:
        slug = slug[:max_length].rstrip("-")
    return slug or FALLBACK
