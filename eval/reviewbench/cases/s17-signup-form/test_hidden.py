import pytest

from module import FormError, check_username, validate_signup


def test_happy_good_and_plainly_bad_forms():
    cleaned = validate_signup({"username": "alice_1", "email": " Alice@Example.test ", "password": "correct horse 9"})
    assert cleaned["username"] == "alice_1" and cleaned["email"] == "alice@example.test"
    with pytest.raises(FormError) as caught:
        validate_signup({"username": "ab", "email": "nope", "password": "short"})
    assert [field for field, _ in caught.value.problems] == ["username", "email", "password"]
    with pytest.raises(ValueError):
        check_username("Admin")


@pytest.mark.parametrize("name", ["bob<script>", "carol smith", "dave" + "x" * 30, "eve\n"])
def test_defect_trailing_junk_is_rejected(name):
    with pytest.raises(ValueError):
        check_username(name)
