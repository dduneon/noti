import sqlite3

from noti.models import Listing
from noti.store import GLOBAL_SCOPE, Store


def make(article_no: str, price_text: str = "8억", **overrides) -> Listing:
    base = {
        "article_no": article_no,
        "name": "○○아파트",
        "trade_type": "전세",
        "price_text": price_text,
        "deposit": 80000,
        "area_m2": 84.97,
        "floor": 7,
        "complex_no": "111515",
    }
    base.update(overrides)
    return Listing(**base)


def test_filter_new_and_remember(tmp_path):
    with Store(tmp_path / "t.db") as store:
        listings = [make("1"), make("2", floor=9)]
        assert len(store.filter_new("t", listings)) == 2

        store.remember("t", listings)
        assert store.filter_new("t", listings) == []

        assert [x.article_no for x in store.filter_new("t", [*listings, make("3", floor=11)])] == [
            "3"
        ]


def test_price_change_notifies_again(tmp_path):
    with Store(tmp_path / "t.db") as store:
        store.remember("t", [make("1")])
        changed = [make("1", "7억 9,000")]
        assert len(store.filter_new("t", changed)) == 1


def test_price_change_can_be_disabled(tmp_path):
    with Store(tmp_path / "t.db") as store:
        store.remember("t", [make("1")])
        changed = [make("1", "7억 9,000")]
        assert store.filter_new("t", changed, notify_on_price_change=False) == []


def test_same_property_from_another_realtor_is_merged(tmp_path):
    """매물번호는 다르지만 같은 단지·면적·층·거래유형이면 한 집으로 본다."""
    with Store(tmp_path / "t.db") as store:
        store.remember("t", [make("1")])
        duplicate = make("999")  # 다른 중개사가 올린 같은 집
        assert store.filter_new("t", [duplicate]) == []

        # 끄면 별개 매물로 취급
        assert len(store.filter_new("t", [duplicate], merge_same_property=False)) == 1


def test_same_property_deduped_within_one_cycle(tmp_path):
    with Store(tmp_path / "t.db") as store:
        fresh = store.filter_new("t", [make("1"), make("2"), make("3", floor=12)])
        assert [x.article_no for x in fresh] == ["1", "3"]


def test_no_fingerprint_when_details_missing(tmp_path):
    """층·면적을 못 읽은 매물은 섣불리 묶지 않는다."""
    assert make("1", floor=None).fingerprint is None
    assert make("1", area_m2=None).fingerprint is None
    with Store(tmp_path / "t.db") as store:
        store.remember("t", [make("1", floor=None)])
        assert len(store.filter_new("t", [make("2", floor=None)])) == 1


def test_global_scope_shares_across_targets(tmp_path):
    with Store(tmp_path / "t.db") as store:
        store.remember(GLOBAL_SCOPE, [make("1")])
        # 다른 감시 대상이 같은 매물을 발견해도 전역 범위면 다시 알리지 않는다
        assert store.filter_new(GLOBAL_SCOPE, [make("1")]) == []
        # 대상별 범위라면 별개
        assert len(store.filter_new("강남구 전세", [make("1")])) == 1


def test_bootstrap_flag(tmp_path):
    with Store(tmp_path / "t.db") as store:
        assert not store.is_bootstrapped("t")
        store.mark_bootstrapped("t")
        assert store.is_bootstrapped("t")


def test_migrates_old_schema(tmp_path):
    """구버전 DB 를 열어도 이미 알린 매물이 다시 알림으로 가지 않는다."""
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE seen_articles (
            target TEXT NOT NULL, article_no TEXT NOT NULL, price_text TEXT,
            first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
            PRIMARY KEY (target, article_no)
        );
        INSERT INTO seen_articles VALUES ('강남구 전세', '1', '8억', '2026-09-01', '2026-09-01');
        """
    )
    conn.commit()
    conn.close()

    with Store(db_path) as store:
        assert store.filter_new("강남구 전세", [make("1")]) == []  # 대상별 범위
        assert store.filter_new(GLOBAL_SCOPE, [make("1")]) == []  # 전역 범위로 바꿔도
        assert store._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'seen_articles'"
        ).fetchone() is None
