from pathlib import Path

import db


def test_customers_table():
    conn = db.connect()
    conn.execute("INSERT INTO customers (name) VALUES ('Ada')")
    assert conn.execute("SELECT name FROM customers").fetchall() == [("Ada",)]


def test_version_matches_latest_migration():
    files = sorted(Path(db.MIGRATIONS).glob("*.sql"))
    numbers = [int(f.name[:3]) for f in files]
    assert numbers == list(range(1, len(numbers) + 1))
    assert db.SCHEMA_VERSION == numbers[-1]
