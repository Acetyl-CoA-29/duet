"""Persistence helpers. Event data is immutable except for privacy deletion."""
import sqlite3


class Store:
    def __init__(self, path=":memory:"):
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS events ("
            "seq INTEGER PRIMARY KEY AUTOINCREMENT, tenant TEXT NOT NULL, "
            "happened INTEGER NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL)"
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS history_order "
            "ON events (tenant, happened DESC, seq DESC)"
        )
        self.connection.commit()

    def append(self, tenant, happened, kind, message):
        """Return the arrival sequence of the new event."""
        cursor = self.connection.execute(
            "INSERT INTO events (tenant, happened, kind, message) VALUES (?, ?, ?, ?)",
            (tenant, happened, kind, message),
        )
        self.connection.commit()
        return cursor.lastrowid

    def delete(self, seq):
        self.connection.execute("DELETE FROM events WHERE seq = ?", (seq,))
        self.connection.commit()

    def high_watermark(self):
        row = self.connection.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()
        return row[0]

    def rows_before(self, tenant, before, limit, kind=None):
        """Legacy reporting query: strict timestamp bound, sorted by time then seq."""
        clauses = ["tenant = ?"]
        params = [tenant]
        if before is not None:
            clauses.append("happened < ?")
            params.append(before)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        params.append(limit)
        query = ("SELECT seq, tenant, happened, kind, message FROM events WHERE "
                 + " AND ".join(clauses) + " ORDER BY happened DESC, seq DESC LIMIT ?")
        return [dict(row) for row in self.connection.execute(query, params)]

    def scan(self, tenant, after, limit, kind=None):
        """Read one bounded snapshot page, independent of the legacy report query."""
        clauses = ["tenant = ?"]
        params = [tenant]
        if after is not None:
            clauses.append("(happened < ? OR (happened = ? AND seq < ?))")
            params.extend([after[0], after[0], after[1]])
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        params.append(limit)
        query = ("SELECT seq, tenant, happened, kind, message FROM events WHERE "
                 + " AND ".join(clauses) + " ORDER BY happened DESC, seq DESC LIMIT ?")
        return [dict(row) for row in self.connection.execute(query, params)]

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
