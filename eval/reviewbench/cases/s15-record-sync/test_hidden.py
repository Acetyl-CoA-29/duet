from module import PushError, Record, report, sync

RECORDS = [Record("a", {"n": 1}), Record("b", {"n": 2}), Record("c", {"n": 3})]


def test_happy_everything_is_pushed_once():
    pushed = []
    result = sync(RECORDS, frozenset({"a"}), lambda record: pushed.append(record.id))
    assert pushed == ["b", "c"] and result.synced == frozenset({"a", "b", "c"})
    assert result.failures == [] and report(result).startswith("pushed 2 of 2")


def test_defect_a_failed_record_is_tried_again_next_time():
    def push(record):
        if record.id == "b":
            raise PushError("timeout")

    first = sync(RECORDS, frozenset(), push)
    assert first.failures == [("b", "timeout")]
    assert first.synced == frozenset({"a", "c"})
    retried = []
    sync(RECORDS, first.synced, lambda record: retried.append(record.id))
    assert retried == ["b"]
