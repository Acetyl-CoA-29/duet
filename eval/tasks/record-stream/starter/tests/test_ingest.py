from dataclasses import FrozenInstanceError
from io import BytesIO

import pytest

from recordstream import (Record, RecordDecoder, RecordError, UploadSession,
                          iter_records, read_file, summarize)


def test_transport_can_split_an_ascii_record():
    decoder = RecordDecoder()
    assert decoder.feed(b'{"id":') == []
    assert decoder.feed(b'1}\n{"id":2}\n') == [
        Record(1, {"id": 1}), Record(2, {"id": 2})]
    assert decoder.finish() == []


def test_batch_and_file_import():
    data = b'{"kind":"start"}\n{"kind":"end"}\n'
    expected = [Record(1, {"kind": "start"}), Record(2, {"kind": "end"})]
    assert list(iter_records([data])) == expected
    handle = BytesIO(data)
    assert list(read_file(handle)) == expected
    assert not handle.closed


def test_upload_snapshots_and_repeated_close():
    upload = UploadSession()
    assert upload.push(b'{"id":1}\n') == 1
    snapshot = upload.records
    snapshot.clear()
    assert upload.records == [Record(1, {"id": 1})]
    assert upload.close() == [Record(1, {"id": 1})]
    assert upload.close() == [Record(1, {"id": 1})]
    with pytest.raises(RuntimeError):
        upload.push(b"")


def test_record_metadata_is_frozen():
    record = Record(3, {"a": 1})
    with pytest.raises(FrozenInstanceError):
        record.line = 9
    record.value["a"] = 2
    assert record.value == {"a": 2}


def test_dashboard_preserves_category_order_and_empty_category():
    records = [Record(2, {"kind": "z"}), Record(5, {"kind": ""}),
               Record(6, {"kind": "a"}), Record(8, {"kind": "z"}),
               Record(9, {"kind": 0}), Record(10, {"extra": True})]
    report = summarize(records)
    assert list(report["counts"].items()) == [("z", 2), ("", 1), ("a", 1), ("unknown", 2)]
    assert report["first_line"] == 2
    assert report["last_line"] == 10
    assert summarize([]) == {"counts": {}, "first_line": None, "last_line": None}


def test_blank_lines_count_toward_source_line_numbers():
    decoder = RecordDecoder()
    assert decoder.feed(b'\n \t\r\n{"id":1}\n') == [Record(3, {"id": 1})]


def test_errors_are_safe_for_request_logs():
    with pytest.raises(RecordError) as caught:
        RecordDecoder().feed(b'{private-not-json}\n')
    assert str(caught.value) == "invalid json record on line 1"
    assert caught.value.line == 1
    assert caught.value.kind == "json"


def test_finished_decoder_rejects_more_input():
    decoder = RecordDecoder()
    assert decoder.finish() == []
    assert decoder.finish() == []
    with pytest.raises(RuntimeError):
        decoder.feed(b"")
