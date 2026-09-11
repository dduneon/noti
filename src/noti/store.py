"""이미 알린 매물을 기억하는 SQLite 저장소."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

from .models import Listing

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_articles (
    target       TEXT NOT NULL,
    article_no   TEXT NOT NULL,
    price_text   TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    PRIMARY KEY (target, article_no)
);
CREATE TABLE IF NOT EXISTS target_state (
    target        TEXT PRIMARY KEY,
    bootstrapped  INTEGER NOT NULL DEFAULT 0,
    last_run_at   TEXT
);
"""


class Store:
    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def is_bootstrapped(self, target: str) -> bool:
        row = self._conn.execute(
            "SELECT bootstrapped FROM target_state WHERE target = ?", (target,)
        ).fetchone()
        return bool(row and row["bootstrapped"])

    def mark_bootstrapped(self, target: str) -> None:
        now = _now()
        self._conn.execute(
            """
            INSERT INTO target_state (target, bootstrapped, last_run_at) VALUES (?, 1, ?)
            ON CONFLICT(target) DO UPDATE SET bootstrapped = 1, last_run_at = excluded.last_run_at
            """,
            (target, now),
        )
        self._conn.commit()

    def filter_new(self, target: str, listings: list[Listing]) -> list[Listing]:
        """아직 기록에 없는 매물만 골라낸다. 가격이 바뀐 매물도 '새 소식'으로 본다."""
        if not listings:
            return []

        known = {
            row["article_no"]: row["price_text"]
            for row in self._conn.execute(
                "SELECT article_no, price_text FROM seen_articles WHERE target = ?", (target,)
            )
        }
        return [
            listing
            for listing in listings
            if listing.article_no not in known or known[listing.article_no] != listing.price_text
        ]

    def remember(self, target: str, listings: list[Listing]) -> None:
        now = _now()
        self._conn.executemany(
            """
            INSERT INTO seen_articles (target, article_no, price_text, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(target, article_no) DO UPDATE SET
                price_text = excluded.price_text,
                last_seen_at = excluded.last_seen_at
            """,
            [(target, listing.article_no, listing.price_text, now, now) for listing in listings],
        )
        self._conn.commit()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
