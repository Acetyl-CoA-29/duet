import sqlite3

import pytest

from module import Pool, fetch_all, fetch_one


def make_pool():
    connection = sqlite3.connect(":memory:")
    connection.execute("create table item (id integer, name text)")
    connection.execute("insert into item values (1, 'bolt'), (2, 'nut')")
    return Pool([connection])


def test_happy_query_returns_rows_and_connection():
    pool = make_pool()
    assert fetch_all(pool, "select id, name from item order by id")[1] == {"id": 2, "name": "nut"}
    assert fetch_one(pool, "select name from item where id = ?", [9]) is None
    assert pool.idle == 1 and pool.busy == 0


def test_defect_failed_query_returns_the_connection():
    pool = make_pool()
    with pytest.raises(sqlite3.OperationalError):
        fetch_all(pool, "select * from no_such_table")
    assert pool.idle == 1 and pool.busy == 0
