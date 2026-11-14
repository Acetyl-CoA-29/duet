"""Black-box oracle. The contract is distributed through the starter documents."""
import base64
import json
import subprocess
import sys

import pytest

from auditfeed import CursorError, Store, export_lines, export_text, list_events


@pytest.fixture
def store():
    with Store() as value:
        yield value


def add(store, times, tenant="north", kind="read"):
    return [store.append(tenant, time, kind, "message-" + str(i))
            for i, time in enumerate(times)]


def collect(store, tenant="north", limit=2, kind=None):
    rows, cursor = [], None
    for _ in range(100):
        page = list_events(store, tenant, limit, cursor, kind)
        assert set(page) == {"items", "next_cursor"}
        assert len(page["items"]) <= limit
        rows.extend(page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            return rows
        assert isinstance(cursor, str) and cursor
    pytest.fail("pagination did not terminate")


@pytest.mark.parametrize("limit", [1, 2, 3, 7, 100])
@pytest.mark.parametrize("kind", [None, "read", ""])
def test_full_traversal_matches_ordered_filtered_log(store, limit, kind):
    add(store, [4, 4, 0, 9, 4, -1, 0])
    add(store, [4, 4, 10], kind="")
    add(store, [3, 4, 20], tenant="south")
    expected = store.rows_before("north", None, 100, kind)
    actual = collect(store, limit=limit, kind=kind)
    assert actual == expected
    assert len({row["seq"] for row in actual}) == len(actual)
    assert all(row["tenant"] == "north" for row in actual)


def test_later_arrivals_do_not_enter_snapshot_even_when_backdated(store):
    ids = add(store, [50, 40, 30, 20, 10])
    first = list_events(store, "north", 2)
    add(store, [1000, 40, 35, 15, -100])
    second = list_events(store, "north", 100, first["next_cursor"])
    assert [row["seq"] for row in first["items"]] == ids[:2]
    assert [row["seq"] for row in second["items"]] == ids[2:]
    assert second["next_cursor"] is None
    assert len(collect(store)) == 10


def test_watermark_propagates_across_more_than_one_continuation(store):
    ids = add(store, [90, 80, 70, 60, 50, 40])
    page = list_events(store, "north", 1)
    seen = [row["seq"] for row in page["items"]]
    for i in range(5):
        add(store, [65 - i, -10 - i])
        page = list_events(store, "north", 1, page["next_cursor"])
        seen.extend(row["seq"] for row in page["items"])
    assert seen == ids
    assert page["next_cursor"] is None


def test_boundary_deletion_and_future_deletion_do_not_shift_position(store):
    ids = add(store, [10, 10, 10, 10, 10, 10])
    first = list_events(store, "north", 2)
    store.delete(ids[4])
    store.delete(ids[1])
    add(store, [9])
    second = list_events(store, "north", 100, first["next_cursor"])
    assert [row["seq"] for row in first["items"]] == [ids[5], ids[4]]
    assert [row["seq"] for row in second["items"]] == [ids[3], ids[2], ids[0]]
    assert second["next_cursor"] is None


@pytest.mark.parametrize("new_tenant,new_kind", [
    ("south", "read"), ("north", None), ("north", ""), ("north", "READ")
])
def test_cursor_is_bound_to_query(store, new_tenant, new_kind):
    add(store, [3, 2, 1])
    token = list_events(store, "north", 1, kind="read")["next_cursor"]
    with pytest.raises(CursorError):
        list_events(store, new_tenant, 1, token, new_kind)
    assert issubclass(CursorError, ValueError)


def test_cursor_can_be_replayed_and_page_size_changed(store):
    ids = add(store, [5, 4, 3, 2, 1])
    token = list_events(store, "north", 1)["next_cursor"]
    a = list_events(store, "north", 2, token)
    b = list_events(store, "north", 2, token)
    assert a == b
    assert [row["seq"] for row in a["items"]] == ids[1:3]
    end = list_events(store, "north", 100, a["next_cursor"])
    assert [row["seq"] for row in end["items"]] == ids[3:]
    assert end["next_cursor"] is None


def test_no_cursor_when_only_nonmatching_rows_remain(store):
    add(store, [9, 8], kind="write")
    add(store, [7, 6, 5])
    add(store, [4, 3], tenant="south", kind="write")
    page = list_events(store, "north", 2, kind="write")
    assert len(page["items"]) == 2
    assert page["next_cursor"] is None
    assert list_events(store, "absent") == {"items": [], "next_cursor": None}


@pytest.mark.parametrize("token", [
    "", "!!", "%%%%", "é", 1, b"bytes",
    base64.urlsafe_b64encode(b"[]").decode(),
    base64.urlsafe_b64encode(b'{"unsupported":true}').decode(),
    base64.urlsafe_b64encode(b"not json").decode(),
])
def test_bad_cursor_has_one_public_error_type(store, token):
    add(store, [3, 2, 1])
    with pytest.raises(CursorError):
        list_events(store, "north", cursor=token)


@pytest.mark.parametrize("size", [0, -1, 101, True, 1.5, "2", None])
def test_size_contract_applies_to_both_consumers(store, size):
    add(store, [2, 1])
    with pytest.raises(ValueError):
        list_events(store, "north", size)
    with pytest.raises(ValueError):
        list(export_lines(store, "north", size))


def test_cursor_survives_process_restart(tmp_path):
    path = tmp_path / "events.db"
    with Store(str(path)) as first_store:
        ids = add(first_store, [10, 10, 9, 8])
        token = list_events(first_store, "north", 1)["next_cursor"]
        add(first_store, [7])
    code = ("import json, sys; from auditfeed import Store, list_events; "
            "s=Store(sys.argv[1]); "
            "print(json.dumps(list_events(s, 'north', 100, sys.argv[2])))")
    done = subprocess.run([sys.executable, "-c", code, str(path), token],
                          check=True, capture_output=True, text=True, timeout=10)
    page = json.loads(done.stdout)
    assert [row["seq"] for row in page["items"]] == [ids[0], ids[2], ids[3]]
    assert page["next_cursor"] is None


def test_export_uses_history_order_and_format_with_ties(store):
    add(store, [10, 10, 10, 9, 8, 8])
    store.append("north", 8, "read", "café\n雪")
    expected = collect(store)
    lines = list(export_lines(store, "north", 2))
    assert [json.loads(line) for line in lines] == expected
    assert all(line == json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
               for line, row in zip(lines, expected))
    assert export_text(store, "north", 1) == "".join(lines)


def test_export_snapshot_starts_on_first_next_and_is_kept(store):
    add(store, [60, 50, 40, 30])
    stream = export_lines(store, "north", 2)
    add(store, [70])
    expected = store.rows_before("north", None, 100)
    first = next(stream)
    assert json.loads(first) == expected[0]
    assert not store.connection.in_transaction
    add(store, [100, 45, -1])
    remainder = list(stream)
    assert [json.loads(line) for line in remainder] == expected[1:]
    assert not store.connection.in_transaction


def test_export_exact_kind_and_empty_results(store):
    add(store, [3, 3, 2], kind="")
    add(store, [9, 8, 7], kind="read")
    add(store, [4, 1], tenant="south", kind="")
    result = [json.loads(line) for line in export_lines(store, "north", 1, "")]
    assert len(result) == 3
    assert all(row["kind"] == "" and row["tenant"] == "north" for row in result)
    assert export_text(store, "north", kind="missing") == ""


def test_legacy_timestamp_query_still_excludes_all_boundary_ties(store):
    add(store, [30, 20, 20, 10], kind="write")
    add(store, [15], kind="")
    assert [row["happened"] for row in store.rows_before("north", 20, 100)] == [15, 10]
    assert len(store.rows_before("north", None, 100)) == 5
    assert [row["happened"] for row in store.rows_before("north", 20, 100, "")] == [15]
    assert store.rows_before("unknown", None, 100) == []
