"""D1 double backed by real SQLite and the production migrations.

``MockD1`` (tests/conftest.py) never executes SQL: a test supplies an
``execute(sql, params)`` callback that matches on SQL text and returns canned
rows, so the queries themselves go untested. ``SqliteD1`` runs every statement
on an in-memory SQLite database built from ``migrations/*.sql``, so tests seed
rows and assert on what the production SQL actually returns.

Checked against D1 as run locally by wrangler 4.71 / miniflare 4.20260301
(the probe is described in the PR that added this file): ``first()`` returns the first
row or ``None``, ``all()`` returns ``{"results": [...]}``, ``run()`` reports
``meta.changes`` (counting trigger writes: an article INSERT reports 3), a
repeated column name keeps its last value, and statements with more than 100
bound parameters are rejected. Known gap: D1 refuses some SQLite functions
(``sqlite_version()`` fails with "not authorized to use function") that this
double allows.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"

# D1 rejects statements with more than 100 bound parameters.
D1_MAX_BOUND_PARAMETERS = 100


def migrated_sqlite() -> sqlite3.Connection:
    """Return an in-memory SQLite connection with every migration applied in order."""
    # TestClient runs the app on another thread; tests use it sequentially.
    conn = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # D1 enforces foreign keys
    for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
        conn.executescript(migration.read_text(encoding="utf-8"))
    return conn


class SqliteD1Statement:
    """A prepared statement that SQLite executes when awaited."""

    def __init__(self, sql: str, db: SqliteD1) -> None:
        self._sql = sql
        self._db = db
        self._params: list[Any] = []

    def bind(self, *args: Any) -> SqliteD1Statement:
        if len(args) > D1_MAX_BOUND_PARAMETERS:
            raise ValueError(f"too many SQL variables: {len(args)} > {D1_MAX_BOUND_PARAMETERS}")
        self._params = list(args)
        return self

    async def all(self) -> dict[str, Any]:
        return {"results": self._db.query(self._sql, self._params), "success": True}

    async def first(self) -> dict[str, Any] | None:
        rows = self._db.query(self._sql, self._params)
        return rows[0] if rows else None

    async def run(self) -> dict[str, Any]:
        return {"success": True, "meta": {"changes": self._db.write(self._sql, self._params)}}


class SqliteD1:
    """D1 binding double: real SQL engine, production schema."""

    def __init__(self, conn: sqlite3.Connection | None = None) -> None:
        self.conn = conn or migrated_sqlite()

    def prepare(self, sql: str) -> SqliteD1Statement:
        return SqliteD1Statement(sql, self)

    def query(self, sql: str, params: list[Any]) -> list[dict[str, Any]]:
        cursor = self.conn.execute(sql, params)
        names = [column[0] for column in cursor.description or ()]
        # Built from pairs, so a repeated column name keeps its last value, as D1 does.
        return [dict(zip(names, tuple(row), strict=True)) for row in cursor.fetchall()]

    def write(self, sql: str, params: list[Any]) -> int:
        # Measured inside a transaction: in autocommit mode the FTS5 index flush
        # at commit adds a change that D1 does not report.
        # A savepoint also nests inside a transaction a test opened itself.
        self.conn.execute("SAVEPOINT d1_write")
        try:
            before = self.conn.total_changes
            self.conn.execute(sql, params).fetchall()
            changes = self.conn.total_changes - before
            self.conn.execute("RELEASE d1_write")
        except BaseException:
            self.conn.execute("ROLLBACK TO d1_write")
            self.conn.execute("RELEASE d1_write")
            raise
        return changes

    # -- seeding helpers -----------------------------------------------------

    def insert(self, table: str, **values: Any) -> None:
        """Insert one row; columns not given take the schema defaults."""
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        self.conn.execute(
            f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", list(values.values())
        )
