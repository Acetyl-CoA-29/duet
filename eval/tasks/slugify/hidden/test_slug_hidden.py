import pytest

from slug import slugify


def test_basic():
    assert slugify("Hello, World!") == "hello-world"


def test_accents_are_stripped():
    assert slugify("Crème Brûlée") == "creme-brulee"
    assert slugify("naïve café") == "naive-cafe"


def test_ligature_is_expanded():
    assert slugify("ﬁne print") == "fine-print"


def test_apostrophes_are_removed():
    assert slugify("Don't stop") == "dont-stop"
    assert slugify("rock’n’roll") == "rocknroll"


def test_underscore_and_dots_are_separators():
    assert slugify("snake_case v2.0") == "snake-case-v2-0"


def test_non_latin_text_is_a_separator():
    assert slugify("tokyo 東京 2020") == "tokyo-2020"


def test_empty_results_fall_back():
    assert slugify("") == "n-a"
    assert slugify("---") == "n-a"
    assert slugify("東京") == "n-a"


def test_truncation_never_leaves_trailing_hyphen():
    assert slugify("hello world", max_length=6) == "hello"
    assert slugify("a b c d", max_length=4) == "a-b"


def test_truncation_keeps_what_fits():
    assert slugify("hello world", max_length=8) == "hello-wo"
    assert slugify("hello world", max_length=50) == "hello-world"


def test_max_length_must_be_positive():
    for bad in (0, -1):
        with pytest.raises(ValueError):
            slugify("abc", max_length=bad)


def test_max_length_must_be_an_int():
    with pytest.raises(ValueError):
        slugify("abc", max_length=2.5)


def test_text_must_be_a_string():
    for bad in (None, 42, b"bytes"):
        with pytest.raises(TypeError):
            slugify(bad)
