import pytest

from module import TransferError, total, transfer


def test_happy_transfer_moves_money():
    before = {"alice": 500, "bob": 100}
    after, receipt = transfer(before, "alice", "bob", 200)
    assert after == {"alice": 300, "bob": 300} and before["alice"] == 500
    assert receipt.amount == 200 and total(after) == total(before)
    with pytest.raises(TransferError):
        transfer(before, "bob", "alice", 101)


def test_defect_negative_amount_is_refused():
    with pytest.raises(TransferError):
        transfer({"alice": 500, "bob": 100}, "alice", "bob", -300)


def test_defect_zero_amount_is_refused():
    with pytest.raises(TransferError):
        transfer({"alice": 500, "bob": 100}, "alice", "bob", 0)
