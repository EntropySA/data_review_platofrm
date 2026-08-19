"""An in-memory stand-in for the D1 binding, backed by SQLite.

Exercising the real SQL is the point: the store's queries are where the logic
lives, and a hand-written fake that only records calls would prove nothing.
"""

import sqlite3
from pathlib import Path

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "0001_initial.sql"


class Meta:
    def __init__(self, changes: int):
        self.changes = changes


class Result:
    def __init__(self, results: list[dict], changes: int = 0):
        self.results = results
        self.meta = Meta(changes)


class Statement:
    def __init__(self, connection, sql, params=()):
        self.connection, self.sql, self.params = connection, sql, params

    def bind(self, *params):
        return Statement(self.connection, self.sql, params)

    def execute(self):
        return self.connection.execute(self.sql, self.params)

    async def all(self):
        return Result([dict(row) for row in self.execute().fetchall()])

    async def first(self):
        row = self.execute().fetchone()
        return dict(row) if row else None

    async def run(self):
        cursor = self.execute()
        return Result([], max(cursor.rowcount, 0))


class FakeDatabase:
    def __init__(self):
        # TestClient runs the app on its own thread; the binding this stands
        # in for has no such affinity, so neither should the fake.
        self.connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(MIGRATION.read_text(encoding="utf-8"))

    def prepare(self, sql):
        return Statement(self.connection, sql)

    async def batch(self, statements):
        results = [Result([], max(statement.execute().rowcount, 0)) for statement in statements]
        self.connection.commit()
        return results

    def rows(self, sql, *params):
        return [dict(row) for row in self.connection.execute(sql, params).fetchall()]
