"""Oracle requirements are mapped to supplied evidence in ../FAIRNESS.md."""

from dataclasses import FrozenInstanceError
from io import BytesIO
import json

import pytest

from recordstream import (Record, RecordDecoder, RecordError, UploadSession,
                          iter_records, read_file, summarize)


def decode(chunks):
    decoder = RecordDecoder()
    records = []
    for chunk in chunks:
        records.extend(decoder.feed(chunk))
    records.extend(decoder.finish())
    return records


def test_every_two_chunk_partition_has_identical_records():
    data = '\ufeff\n \t\r\n{"label":"naïve 🐙","a":1}\r\n{"tail":true}'.encode("utf-8")
    expected = [Record(3, {"label": "naïve 🐙", "a": 1}), Record(4, {"tail": True})]
    for split in range(len(data) + 1):
        assert decode([data[:split], b"", data[split:]]) == expected
    assert decode([bytes([byte]) for byte in data]) == expected


def test_complete_json_prefix_waits_for_a_delimiter_or_eof():
    decoder = RecordDecoder()
    assert decoder.feed(b'{"id":1}') == []
    assert decoder.feed(b"") == []
    assert decoder.feed(b"\r") == []
    assert decoder.feed(b"\n") == [Record(1, {"id": 1})]
    assert decoder.feed(b'{"id":2}') == []
    assert decoder.finish() == [Record(2, {"id": 2})]
    assert decoder.finish() == []


@pytest.mark.parametrize("data", [b"", b"\n", b" \t\r", b"\n\n \t\r\n",
                                  b"\xef\xbb\xbf", b"\xef\xbb\xbf \n\n"])
def test_empty_or_blank_exports(data):
    assert decode([data]) == []
    assert list(iter_records([b"", data, b""])) == []


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\x85"])
def test_unicode_line_separators_inside_json_strings_are_content(separator):
    payload = json.dumps({"text": "a" + separator + "b"}, ensure_ascii=False).encode("utf-8")
    assert decode([payload + b"\n{}"]) == [Record(1, {"text": "a" + separator + "b"}), Record(2, {})]


@pytest.mark.parametrize("data,kind", [
    (b"\xff", "utf8"), (b"\xe2\x82", "utf8"), (b'{"a":', "json"),
    (b"[]", "object"), (b"null", "object"), (b'"text"', "object"),
    (b"true", "object"), (b"42", "object"), (b"{}\r{}", "json"),
    (b"{}\x0b{}", "json"), (b"\xc2\xa0", "json"), (b"\x0c", "json"),
])
def test_error_classification_at_eof_and_terminated_lines(data, kind):
    for terminated in (False, True):
        decoder = RecordDecoder()
        assert decoder.feed(b"\n \t\r\n") == []
        if terminated:
            operation = lambda: decoder.feed(data + b"\n")
        else:
            assert decoder.feed(data) == []
            operation = decoder.finish
        with pytest.raises(RecordError) as caught:
            operation()
        assert caught.value.line == 3
        assert caught.value.kind == kind
        assert str(caught.value) == "invalid {} record on line 3".format(kind)


@pytest.mark.parametrize("data", [b"\n\xef\xbb\xbf{}\n", b"{}\n\xef\xbb\xbf{}\n",
                                  b" \xef\xbb\xbf{}\n", b"\xef\xbb\xbf\xef\xbb\xbf{}\n"])
def test_only_one_bom_at_absolute_zero_is_special(data):
    with pytest.raises(RecordError) as caught:
        decode([data])
    assert caught.value.kind == "json"
    assert caught.value.line == (2 if b"\n" in data[:-1] else 1)


def test_bom_inside_a_value_is_preserved():
    assert decode(['{"x":"\ufeff"}\n'.encode("utf-8")]) == [Record(1, {"x": "\ufeff"})]


def test_stdlib_json_semantics_and_extension_fields_survive():
    assert decode([b'{"a":1,"a":2,"extension":{"x":[false,null,3]}}\n']) == [
        Record(1, {"a": 2, "extension": {"x": [False, None, 3]}})]


@pytest.mark.parametrize("at_eof", [False, True])
def test_decode_failure_is_terminal_and_preserves_exception_identity(at_eof):
    decoder = RecordDecoder()
    assert decoder.feed(b'{}\n') == [Record(1, {})]
    if at_eof:
        assert decoder.feed(b'{"unfinished":') == []
        operation = decoder.finish
    else:
        operation = lambda: decoder.feed(b"\xff\n")
    with pytest.raises(RecordError) as original:
        operation()
    for retry in (lambda: decoder.feed(b""), lambda: decoder.feed(b"{}\n"), decoder.finish):
        with pytest.raises(RecordError) as caught:
            retry()
        assert caught.value is original.value
        assert caught.value.line == 2


def test_upload_does_not_publish_any_prefix_of_a_failing_push():
    upload = UploadSession()
    assert upload.push(b'{"saved":1}\n') == 1
    with pytest.raises(RecordError) as original:
        upload.push(b'{"not_delivered":2}\n\n[]\n')
    assert original.value.line == 4
    assert upload.records == [Record(1, {"saved": 1})]
    for retry in (lambda: upload.push(b'{"retry":3}\n'), upload.close, upload.close):
        with pytest.raises(RecordError) as caught:
            retry()
        assert caught.value is original.value
        assert upload.records == [Record(1, {"saved": 1})]


def test_batch_does_not_yield_any_prefix_of_a_failing_feed():
    records = iter_records([b'{"saved":1}\n', b'{"not_delivered":2}\n[]\n'])
    assert next(records) == Record(1, {"saved": 1})
    with pytest.raises(RecordError) as caught:
        next(records)
    assert caught.value.kind == "object"
    assert caught.value.line == 3


def test_live_upload_flushes_once_and_returns_independent_lists():
    upload = UploadSession()
    assert upload.push(b'\n{"id":') == 0
    assert upload.records == []
    assert upload.push(b'1}\r\n{"id":2}') == 1
    assert upload.records == [Record(2, {"id": 1})]
    final = upload.close()
    expected = [Record(2, {"id": 1}), Record(3, {"id": 2})]
    assert final == expected
    final.clear()
    assert upload.close() == expected
    snapshot = upload.records
    snapshot.pop()
    assert upload.records == expected
    with pytest.raises(RuntimeError):
        upload.push(b"")


def test_live_upload_error_at_close_stays_an_error():
    upload = UploadSession()
    assert upload.push(b'{}\n\n{"incomplete":') == 1
    with pytest.raises(RecordError) as original:
        upload.close()
    assert original.value.line == 3
    assert original.value.kind == "json"
    assert upload.records == [Record(1, {})]
    for retry in (upload.close, lambda: upload.push(b"}")):
        with pytest.raises(RecordError) as caught:
            retry()
        assert caught.value is original.value


def test_batch_iterator_is_lazy_in_transport_reads():
    events = []

    def source():
        events.append("first")
        yield b'{"id":1}\n{"id":2}\n'
        events.append("second")
        yield b'{"id":3}'
        events.append("eof")

    records = iter_records(source())
    assert events == []
    assert next(records) == Record(1, {"id": 1})
    assert events == ["first"]
    assert next(records) == Record(2, {"id": 2})
    assert events == ["first"]
    assert next(records) == Record(3, {"id": 3})
    assert events == ["first", "second", "eof"]
    assert list(records) == []


def test_transport_exception_does_not_flush_partial_record():
    original = OSError("connection interrupted")

    def source():
        yield b'{}\n{"valid_but_unterminated":1}'
        raise original

    records = iter_records(source())
    assert next(records) == Record(1, {})
    with pytest.raises(OSError) as caught:
        next(records)
    assert caught.value is original


@pytest.mark.parametrize("size", [1, 2, 5, 4096])
def test_binary_file_adapter_uses_current_position_and_finalizes(size):
    handle = BytesIO(b'ignored-prefix' + '\n{"city":"東京"}\r\n{"n":2}'.encode("utf-8"))
    handle.seek(len(b"ignored-prefix"))
    expected = [Record(2, {"city": "東京"}), Record(3, {"n": 2})]
    assert list(read_file(handle, size)) == expected
    assert not handle.closed
    assert handle.read() == b""


@pytest.mark.parametrize("size", [0, -1])
def test_binary_file_adapter_rejects_nonpositive_chunk_size(size):
    with pytest.raises(ValueError):
        list(read_file(BytesIO(b"{}"), size))


def test_report_and_record_compatibility_is_not_lost_in_the_refactor():
    records = decode([b'\n{"kind":"z"}\n{"kind":""}\n\n{"kind":false}\n{"extra":1}'])
    report = summarize(iter(records))
    assert list(report["counts"].items()) == [("z", 1), ("", 1), ("unknown", 2)]
    assert report["first_line"] == 2
    assert report["last_line"] == 6
    with pytest.raises(FrozenInstanceError):
        records[0].line = 42
    records[0].value["kind"] = "changed"
    assert records[0].value["kind"] == "changed"


def test_successful_finish_is_terminal_but_idempotent():
    decoder = RecordDecoder()
    assert decoder.feed(b"{}") == []
    assert decoder.finish() == [Record(1, {})]
    assert decoder.finish() == []
    for data in (b"", b"\n", b"{}\n"):
        with pytest.raises(RuntimeError):
            decoder.feed(data)
