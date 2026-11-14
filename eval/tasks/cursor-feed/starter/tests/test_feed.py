import json

from auditfeed import Store, export_text, list_events


def test_imported_events_are_not_skipped():
    with Store() as store:
        for message in ("one", "two", "three"):
            store.append("north", 100, "import", message)
        page = list_events(store, "north", limit=2)
        assert [row["message"] for row in page["items"]] == ["three", "two"]
        following = list_events(store, "north", limit=2, cursor=page["next_cursor"])
        assert [row["message"] for row in following["items"]] == ["one"]
        assert following["next_cursor"] is None


def test_tenant_and_kind():
    with Store() as store:
        store.append("north", 4, "write", "yes")
        store.append("south", 5, "write", "other tenant")
        store.append("north", 6, "read", "other kind")
        assert [row["message"] for row in list_events(store, "north", kind="write")["items"]] == ["yes"]


def test_export_format():
    with Store() as store:
        seq = store.append("north", 20, "read", "café")
        expected = {"seq": seq, "tenant": "north", "happened": 20,
                    "kind": "read", "message": "café"}
        assert export_text(store, "north", batch_size=2) == json.dumps(
            expected, ensure_ascii=False, sort_keys=True) + "\n"


def test_external_reporting_timestamp_bound():
    with Store() as store:
        store.append("north", 10, "read", "older")
        store.append("north", 20, "read", "at boundary")
        assert [row["happened"] for row in store.rows_before("north", 20, 10)] == [10]
        assert len(store.rows_before("north", None, 10)) == 2
