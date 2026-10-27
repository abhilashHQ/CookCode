"""Applies migrations/NNN_*.sql in order. Shipped migrations never change; a schema change
is a new file, and SCHEMA_VERSION names the latest one."""
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1
MIGRATIONS = Path(__file__).parent / "migrations"


def connect(path=":memory:"):
    db = sqlite3.connect(path)
    for file in sorted(MIGRATIONS.glob("*.sql")):
        db.executescript(file.read_text())
    return db
