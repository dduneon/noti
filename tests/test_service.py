import pytest

from noti.config import Criteria, Settings, Target, WatchConfig
from noti.models import Listing
from noti.service import MonitorService
from noti.store import Store


class FakeClient:
    def __init__(self, listings: list[Listing]) -> None:
        self.listings = listings
        self.calls = 0

    async def fetch_listings(self, target: Target) -> list[Listing]:
        self.calls += 1
        return self.listings


class RecordingNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.texts: list[str] = []

    async def send(self, target_name: str, listing: Listing) -> None:
        self.sent.append((target_name, listing.article_no))

    async def send_text(self, text: str) -> None:
        self.texts.append(text)

    async def aclose(self) -> None:
        return None


def make(article_no: str, deposit: int = 80000) -> Listing:
    return Listing(
        article_no=article_no,
        name="○○아파트",
        trade_type="전세",
        price_text="8억",
        deposit=deposit,
        area_m2=84.9,
        floor=7,
    )


def build(tmp_path, listings, *, notify_on_first_run=False):
    settings = Settings(db_path=tmp_path / "t.db", request_delay_seconds=0, jitter_seconds=0)
    config = WatchConfig(
        notify_on_first_run=notify_on_first_run,
        targets=[
            Target(
                name="테스트",
                kind="complex",
                complex_no="1",
                criteria=Criteria(max_deposit=90000),
            )
        ],
    )
    client = FakeClient(listings)
    store = Store(settings.db_path)
    notifier = RecordingNotifier()
    return MonitorService(settings, config, client, store, notifier), client, store, notifier


@pytest.mark.asyncio
async def test_first_run_is_silent_then_notifies_new(tmp_path):
    service, client, store, notifier = build(tmp_path, [make("1"), make("2")])

    await service.run_once()
    assert notifier.sent == []  # 첫 실행은 기록만

    client.listings = [make("1"), make("2"), make("3")]
    await service.run_once()
    assert notifier.sent == [("테스트", "3")]

    await service.run_once()
    assert len(notifier.sent) == 1  # 중복 알림 없음
    store.close()


@pytest.mark.asyncio
async def test_criteria_filters_out_expensive(tmp_path):
    service, _client, store, notifier = build(
        tmp_path, [make("1", deposit=120000)], notify_on_first_run=True
    )
    await service.run_once()
    assert notifier.sent == []
    store.close()


@pytest.mark.asyncio
async def test_cycle_limit(tmp_path):
    listings = [make(str(i)) for i in range(30)]
    service, _client, store, notifier = build(tmp_path, listings, notify_on_first_run=True)
    await service.run_once()
    assert len(notifier.sent) == 20
    assert notifier.texts and "30건" in notifier.texts[0]
    store.close()
