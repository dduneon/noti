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


@pytest.mark.asyncio
async def test_config_is_reloaded_when_file_changes(tmp_path):
    """설정 페이지에서 저장하면 재시작 없이 다음 사이클에 반영된다."""
    from noti.config import WatchConfig

    config_path = tmp_path / "config.yaml"
    service, client, store, _notifier = build(tmp_path, [make("1")], notify_on_first_run=True)
    service._settings.config_path = config_path

    WatchConfig(notify_on_first_run=True, targets=[]).save(config_path)
    await service.run_once()  # mtime 기준점만 잡는다
    assert client.calls == 1

    new_target = Target(name="바뀐대상", kind="region", cortar_no="1168010100")
    WatchConfig(notify_on_first_run=True, targets=[new_target]).save(config_path)
    import os

    os.utime(config_path, (0, 0))  # mtime 을 확실히 다르게
    await service.run_once()

    assert [t.name for t in service._config.targets] == ["바뀐대상"]
    store.close()


@pytest.mark.asyncio
async def test_broken_config_keeps_previous(tmp_path):
    config_path = tmp_path / "config.yaml"
    service, _client, store, _notifier = build(tmp_path, [make("1")])
    service._settings.config_path = config_path

    config_path.write_text("targets: []\n", encoding="utf-8")
    await service.run_once()

    config_path.write_text("targets: [{kind: region}]\n", encoding="utf-8")  # name 없음 → 검증 실패
    import os

    os.utime(config_path, (0, 0))
    await service.run_once()

    assert [t.name for t in service._config.targets] == ["테스트"]  # 이전 설정 유지
    store.close()
