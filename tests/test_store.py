from noti.models import Listing
from noti.store import Store


def make(article_no: str, price_text: str = "8억") -> Listing:
    return Listing(
        article_no=article_no, name="집", trade_type="전세", price_text=price_text, deposit=80000
    )


def test_filter_new_and_remember(tmp_path):
    with Store(tmp_path / "t.db") as store:
        listings = [make("1"), make("2")]
        assert len(store.filter_new("t", listings)) == 2

        store.remember("t", listings)
        assert store.filter_new("t", listings) == []

        # 가격이 바뀌면 다시 알림 대상
        changed = [make("1", "7억 9,000")]
        assert len(store.filter_new("t", changed)) == 1

        # 새 매물만 골라낸다
        assert [x.article_no for x in store.filter_new("t", [*listings, make("3")])] == ["3"]


def test_bootstrap_flag(tmp_path):
    with Store(tmp_path / "t.db") as store:
        assert not store.is_bootstrapped("t")
        store.mark_bootstrapped("t")
        assert store.is_bootstrapped("t")
