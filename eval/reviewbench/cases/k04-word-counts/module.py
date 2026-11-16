"""Count words in text for a simple search index."""
import re
from collections import Counter
from typing import Dict, FrozenSet, Iterable, List, Tuple

WORD_PATTERN = re.compile(r"[a-z0-9']+")
DEFAULT_STOP_WORDS: FrozenSet[str] = frozenset({"a", "an", "and", "of", "or", "the", "to"})
DEFAULT_MIN_LENGTH = 2


def check_min_length(min_length: int) -> int:
    if isinstance(min_length, bool) or not isinstance(min_length, int) or min_length < 1:
        raise ValueError("min_length must be a whole number of at least 1")
    return min_length


def words(text: str) -> List[str]:
    """Every word in `text`, lower-cased, in order of appearance. Apostrophes at either end of a
    word are dropped, and a run made only of apostrophes is not a word."""
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    stripped = (match.strip("'") for match in WORD_PATTERN.findall(text.lower()))
    return [word for word in stripped if word]


def count_words(text: str, stop_words: Iterable[str] = DEFAULT_STOP_WORDS,
                min_length: int = DEFAULT_MIN_LENGTH) -> Dict[str, int]:
    """How often each word occurs, leaving out stop words and words shorter than `min_length`."""
    shortest = check_min_length(min_length)
    skipped = frozenset(word.lower() for word in stop_words)
    kept = (word for word in words(text) if len(word) >= shortest and word not in skipped)
    return dict(Counter(kept))


def top_words(text: str, count: int, stop_words: Iterable[str] = DEFAULT_STOP_WORDS,
              min_length: int = DEFAULT_MIN_LENGTH) -> List[Tuple[str, int]]:
    """The `count` most frequent words with their counts: most frequent first, ties alphabetical."""
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("count must be a whole number that is not negative")
    counted = count_words(text, stop_words, min_length)
    ranked = sorted(counted.items(), key=lambda item: (-item[1], item[0]))
    return ranked[:count]


def shared_words(first: str, second: str) -> List[str]:
    """Words that both texts use, in alphabetical order."""
    return sorted(set(count_words(first)) & set(count_words(second)))
