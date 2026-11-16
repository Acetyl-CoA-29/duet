import pytest

from module import count_words, shared_words, top_words, words


def test_happy_counting_and_ranking():
    text = "The cat and the hat. The Cat's hat isn't a 'bat' -- it's THE hat!"
    assert words("Don't 'quote' ''") == ["don't", "quote"]
    counts = count_words(text)
    assert counts["hat"] == 3 and counts["cat"] == 1 and counts["cat's"] == 1 and "the" not in counts
    assert top_words(text, 2) == [("hat", 3), ("bat", 1)]
    assert top_words(text, 0) == [] and top_words("", 5) == []
    assert count_words("a an I x", stop_words=(), min_length=1) == {"a": 1, "an": 1, "i": 1, "x": 1}
    assert shared_words("red fish blue fish", "one fish, two FISH; red") == ["fish", "red"]
    with pytest.raises(ValueError):
        top_words(text, -1)
    with pytest.raises(ValueError):
        count_words(text, min_length=0)
