"""Run read-only queries on connections borrowed from a small fixed pool."""
from typing import Any, Dict, List, Optional, Sequence


class PoolExhausted(Exception):
    """Every connection is checked out."""


class Pool:
    def __init__(self, connections: Sequence[Any]) -> None:
        if not connections:
            raise ValueError("a pool needs at least one connection")
        self._idle: List[Any] = list(connections)
        self._busy: List[Any] = []

    @property
    def idle(self) -> int:
        return len(self._idle)

    @property
    def busy(self) -> int:
        return len(self._busy)

    def acquire(self) -> Any:
        if not self._idle:
            raise PoolExhausted("every connection is in use")
        connection = self._idle.pop()
        self._busy.append(connection)
        return connection

    def release(self, connection: Any) -> None:
        if not any(connection is held for held in self._busy):
            raise ValueError("this connection is not checked out of this pool")
        self._busy = [held for held in self._busy if held is not connection]
        self._idle.append(connection)


def check_query(sql: str) -> str:
    statement = sql.strip()
    if not statement.lower().startswith("select"):
        raise ValueError("only SELECT statements may be run here")
    return statement


def fetch_all(pool: Pool, sql: str, parameters: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    """Run one SELECT and return its rows as dictionaries keyed by column name."""
    statement = check_query(sql)
    connection = pool.acquire()
    try:
        cursor = connection.execute(statement, tuple(parameters))
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        pool.release(connection)


def fetch_one(pool: Pool, sql: str, parameters: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
    """The first row of the result, or None when there is none."""
    rows = fetch_all(pool, sql, parameters)
    return rows[0] if rows else None
