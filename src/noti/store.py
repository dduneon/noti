"""이미 알린 매물을 기억하는 SQLite 저장소.

키는 두 종류를 함께 기록한다.
- article: 네이버 매물번호. 같은 매물의 재등장을 막는다.
- fingerprint: 단지·면적·층·거래유형 지문. 같은 집을 여러 중개사가 올린 경우를 묶는다.

둘 중 하나라도 기록에 있으면 '이미 아는 집'으로 본다.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

from .models import Listing

logger = logging.getLogger(__name__)

GLOBAL_SCOPE = "__global__"  # dedupe_scope=global 일 때 쓰는 공용 범위

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_keys (
    scope         TEXT NOT NULL,
    key           TEXT NOT NULL,
    kind          TEXT NOT NULL,
    article_no    TEXT NOT NULL,
    price_text    TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    PRIMARY KEY (scope, key)
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
        self._migrate_seen_articles()
        self._conn.commit()

    def _migrate_seen_articles(self) -> None:
        """구버전 seen_articles 를 새 스키마로 옮긴다.

        기존 기록을 대상별 범위와 전역 범위 양쪽에 넣어 둔다.
        dedupe_scope 를 어느 쪽으로 쓰든 이미 알린 매물이 다시 오지 않게 하기 위해서다.
        """
        exists = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'seen_articles'"
        ).fetchone()
        if not exists:
            return

        rows = self._conn.execute("SELECT * FROM seen_articles").fetchall()
        self._conn.executemany(
            """
            INSERT OR IGNORE INTO seen_keys
                (scope, key, kind, article_no, price_text, first_seen_at, last_seen_at)
            VALUES (?, ?, 'article', ?, ?, ?, ?)
            """,
            [
                (scope, row["article_no"], row["article_no"], row["price_text"],
                 row["first_seen_at"], row["last_seen_at"])
                for row in rows
                for scope in (row["target"], GLOBAL_SCOPE)
            ],
        )
        self._conn.execute("DROP TABLE seen_articles")
        logger.info("이전 기록 %d건을 새 형식으로 옮겼습니다", len(rows))

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

    def filter_new(
        self,
        scope: str,
        listings: list[Listing],
        *,
        merge_same_property: bool = True,
        notify_on_price_change: bool = True,
    ) -> list[Listing]:
        """알림을 보낼 매물만 골라낸다.

        이미 아는 집은 제외하고, 같은 사이클 안의 중복(같은 집을 올린 다른 매물)도 한 건만 남긴다.
        notify_on_price_change 가 켜져 있으면 가격이 달라진 매물은 다시 알림 대상으로 본다.
        """
        if not listings:
            return []

        known = {
            row["key"]: row["price_text"]
            for row in self._conn.execute(
                "SELECT key, price_text FROM seen_keys WHERE scope = ?", (scope,)
            )
        }

        fresh: list[Listing] = []
        batch: dict[str, str] = {}  # 이번 사이클에서 이미 챙긴 키
        for listing in listings:
            keys = _keys_for(listing, merge_same_property)
            prices = [known[key] for key in keys if key in known]
            batch_hit = any(key in batch for key in keys)

            if batch_hit:
                continue  # 같은 집이 이번 사이클에 여러 건 올라왔다
            if prices and not (notify_on_price_change and listing.price_text not in prices):
                continue  # 이미 아는 집이고, 가격도 그대로거나 가격 알림을 끔

            fresh.append(listing)
            batch.update(dict.fromkeys(keys, listing.price_text))

        return fresh

    def remember(
        self, scope: str, listings: list[Listing], *, merge_same_property: bool = True
    ) -> None:
        now = _now()
        rows = [
            (scope, key, kind, listing.article_no, listing.price_text, now, now)
            for listing in listings
            for key, kind in _keys_with_kind(listing, merge_same_property)
        ]
        self._conn.executemany(
            """
            INSERT INTO seen_keys
                (scope, key, kind, article_no, price_text, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope, key) DO UPDATE SET
                article_no = excluded.article_no,
                price_text = excluded.price_text,
                last_seen_at = excluded.last_seen_at
            """,
            rows,
        )
        self._conn.commit()


def _keys_with_kind(listing: Listing, merge_same_property: bool) -> list[tuple[str, str]]:
    keys = [(listing.article_no, "article")]
    if merge_same_property and listing.fingerprint:
        keys.append((listing.fingerprint, "fingerprint"))
    return keys


def _keys_for(listing: Listing, merge_same_property: bool) -> list[str]:
    return [key for key, _ in _keys_with_kind(listing, merge_same_property)]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
