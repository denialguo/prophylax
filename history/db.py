"""The history database (M15): stdlib sqlite3, numbered forward-only migrations applied
by PRAGMA user_version. A database newer than the code is refused."""
import glob
import os
import sqlite3

MIGRATIONS = sorted(glob.glob(os.path.join(os.path.dirname(__file__), "migrations", "[0-9][0-9][0-9][0-9]_*.sql")))
SCHEMA_VERSION = len(MIGRATIONS)


def connect(path: str) -> sqlite3.Connection:
    if path != ":memory:":
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current > SCHEMA_VERSION:
        raise RuntimeError(f"history database is at schema {current}; this code knows {SCHEMA_VERSION}")
    for number, path in enumerate(MIGRATIONS, 1):
        if number <= current:
            continue
        with open(path) as fh:
            sql = fh.read()
        # executescript commits first; each migration then runs in its own transaction
        conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
