from slug import slugify


def test_words_are_joined_with_hyphens():
    assert slugify("Hello World") == "hello-world"


def test_extra_spaces_collapse():
    assert slugify("  A   B ") == "a-b"


def test_punctuation_is_a_separator():
    assert slugify("Hello, World!") == "hello-world"


def test_max_length_cuts_the_slug():
    assert slugify("hello world", max_length=5) == "hello"
