"""Move money between accounts in an in-memory ledger. Amounts are whole cents."""
from typing import Dict, List, Mapping, NamedTuple, Sequence, Tuple


class TransferError(Exception):
    """The transfer was refused; the ledger is unchanged."""


class Receipt(NamedTuple):
    source: str
    target: str
    amount: int
    source_balance: int
    target_balance: int


def check_account(ledger: Mapping[str, int], name: str) -> None:
    if name not in ledger:
        raise TransferError(f"no such account: {name}")


def transfer(ledger: Mapping[str, int], source: str, target: str, amount: int) -> Tuple[Dict[str, int], Receipt]:
    """Move `amount` cents from `source` to `target`. Returns a new ledger and a receipt; the ledger
    passed in is left as it was."""
    check_account(ledger, source)
    check_account(ledger, target)
    if source == target:
        raise TransferError("source and target are the same account")
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise TransferError("amount must be a whole number of cents")
    if ledger[source] < amount:
        raise TransferError(f"insufficient funds in {source}")
    updated = {**ledger, source: ledger[source] - amount, target: ledger[target] + amount}
    return updated, Receipt(source, target, amount, updated[source], updated[target])


def transfer_all(ledger: Mapping[str, int],
                 moves: Sequence[Tuple[str, str, int]]) -> Tuple[Dict[str, int], List[Receipt]]:
    """Apply several transfers in order. If any is refused none takes effect, because every step
    works on a copy."""
    current: Dict[str, int] = dict(ledger)
    receipts: List[Receipt] = []
    for source, target, amount in moves:
        current, receipt = transfer(current, source, target, amount)
        receipts = [*receipts, receipt]
    return current, receipts


def total(ledger: Mapping[str, int]) -> int:
    """All the money in the ledger; a transfer never changes it."""
    return sum(ledger.values())
