from module import get_page, page_count, page_numbers


def test_happy_partial_last_page():
    page = get_page(list(range(7)), 2, 5)
    assert page["items"] == [5, 6] and page["pages"] == 2 and not page["has_next"]
    assert page_numbers(1, 2) == [1, 2]


def test_defect_full_last_page_is_the_last():
    assert page_count(10, 5) == 2
    assert get_page(list(range(10)), 2, 5)["has_next"] is False
