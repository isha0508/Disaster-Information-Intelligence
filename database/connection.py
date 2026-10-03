"""Short-lived SQLite connections and explicit transaction boundaries."""

from contextlib import contextmanager
import sqlite3

from database.config import get_database_path


def connect(path=None, *, timeout=10.0):
    """Open a configured SQLite connection with row mappings and FK checks."""
    resolved = get_database_path(path)
    if resolved != ":memory:":
        resolved.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(resolved), timeout=timeout)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    if resolved != ":memory:":
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
    return connection


@contextmanager
def transaction(path=None):
    """Yield a connection and commit once; roll back on any exception and close."""
    connection = connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
