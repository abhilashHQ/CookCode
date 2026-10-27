"""Fact store: SQLite with an FTS5 index, one database per repository.

A fact is an atomic statement about the repository with provenance: the files it cites, their
content hashes when it was written, and the commit. An `updates` fact replaces an older one,
which stays for history but is no longer latest. `extends` and `derives` only link facts."""

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from harness.core.ids import new_id, utc_now

Relation = Literal["updates", "extends", "derives"]
WORD = re.compile(r"[A-Za-z0-9_]{2,}")

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    paths TEXT NOT NULL,
    hashes TEXT NOT NULL,
    git_commit TEXT NOT NULL,
    relation TEXT,
    target TEXT,
    latest INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(id UNINDEXED, text);
"""


@dataclass(frozen=True)
class Fact:
    id: str
    text: str
    hashes: dict[str, str]
    commit: str
    relation: Relation | None
    target: str | None
    latest: bool
    created_at: str

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(self.hashes)


def _fact(row: sqlite3.Row) -> Fact:
    return Fact(
        id=row["id"],
        text=row["text"],
        hashes=json.loads(row["hashes"]),
        commit=row["git_commit"],
        relation=row["relation"],
        target=row["target"],
        latest=bool(row["latest"]),
        created_at=row["created_at"],
    )


class FactStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "FactStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def add(
        self,
        text: str,
        hashes: dict[str, str],
        commit: str,
        relation: Relation | None = None,
        target: str | None = None,
    ) -> Fact:
        fact_id = f"f{new_id()[:8]}"
        with self._db:
            if relation == "updates" and target is not None:
                self._db.execute("UPDATE facts SET latest = 0 WHERE id = ?", (target,))
            self._db.execute(
                "INSERT INTO facts (id, text, paths, hashes, git_commit, relation, target, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (fact_id, text, json.dumps(sorted(hashes)), json.dumps(hashes), commit,
                 relation, target, utc_now().isoformat()),
            )
            self._db.execute("INSERT INTO facts_fts (id, text) VALUES (?, ?)", (fact_id, text))
        fact = self.get(fact_id)
        assert fact is not None
        return fact

    def get(self, fact_id: str) -> Fact | None:
        row = self._db.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()
        return None if row is None else _fact(row)

    def latest(self) -> list[Fact]:
        """Latest facts, newest first."""
        rows = self._db.execute(
            "SELECT * FROM facts WHERE latest = 1 ORDER BY created_at DESC, rowid DESC"
        )
        return [_fact(row) for row in rows]

    def search(self, query: str) -> list[Fact]:
        """Latest facts matching any word of the query, best match first."""
        words = sorted({word.lower() for word in WORD.findall(query)})
        if not words:
            return []
        match = " OR ".join(f'"{word}"' for word in words)
        rows = self._db.execute(
            "SELECT facts.* FROM facts_fts JOIN facts ON facts.id = facts_fts.id "
            "WHERE facts_fts MATCH ? AND facts.latest = 1 ORDER BY bm25(facts_fts)",
            (match,),
        )
        return [_fact(row) for row in rows]
