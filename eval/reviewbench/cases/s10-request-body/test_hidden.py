import io

import pytest

from module import BodyError, BodyTooLarge, parse_json_body, read_body, require_fields


class Endless:
    """A stream that never ends; an unbounded read of it can only fail."""

    def read(self, size=-1):
        if size is None or size < 0:
            raise MemoryError("unbounded read of an endless stream")
        return b"x" * size


def test_happy_small_body_and_limit():
    body = parse_json_body(io.BytesIO(b'{"name": "bolt", "qty": 3}'))
    assert require_fields(body, ["name"]) == {"name": "bolt"}
    with pytest.raises(BodyTooLarge):
        read_body(io.BytesIO(b"x" * 11), max_bytes=10)
    assert read_body(io.BytesIO(b"x" * 10), max_bytes=10) == b"x" * 10
    with pytest.raises(BodyError):
        parse_json_body(io.BytesIO(b"[1]"))


def test_defect_endless_body_is_refused_without_reading_it_all():
    with pytest.raises(BodyTooLarge):
        read_body(Endless(), max_bytes=1024)
