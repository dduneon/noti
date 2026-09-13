import pytest
from fastapi.testclient import TestClient

from noti.config import Settings, WatchConfig
from noti.web import create_app

# 모바일(m.land) 응답 모양
REGION_TREE = {
    "0000000000": [{"cortarNo": "1100000000", "cortarNm": "서울시", "lat": 37.56, "lon": 126.97}],
    "1100000000": [{"cortarNo": "1168000000", "cortarNm": "강남구", "lat": 37.51, "lon": 127.04}],
    "1168000000": [{"cortarNo": "1168010100", "cortarNm": "역삼동", "lat": 37.50, "lon": 127.03}],
    "1168010100": [],
}

ARTICLES = [
    {"atclNo": "1", "atclNm": "○○아파트", "tradTpNm": "전세", "prc": 80000, "hanPrc": "8억",
     "spc2": 84.9, "flrInfo": "7/15"},
    {"atclNo": "2", "atclNm": "△△아파트", "tradTpNm": "전세", "prc": 150000, "hanPrc": "15억",
     "spc2": 114.0, "flrInfo": "3/15"},
]


@pytest.fixture
def client(tmp_path, monkeypatch):
    settings = Settings(
        config_path=tmp_path / "config.yaml",
        db_path=tmp_path / "noti.db",
        telegram_bot_token=None,
        telegram_chat_id=None,
    )
    WatchConfig(targets=[]).save(settings.config_path)
    app = create_app(settings)

    with TestClient(app) as test_client:
        async def fake_get_json(path, params, **kwargs):
            if path == "/map/getRegionList":
                return {"result": {"list": REGION_TREE.get(str(params["cortarNo"]), [])}}
            return {"body": ARTICLES, "more": False}

        monkeypatch.setattr(app.state.client, "_get_json", fake_get_json)
        test_client.settings = settings
        yield test_client


def region_target(**overrides):
    target = {
        "name": "강남구 전세",
        "kind": "region",
        "cortar_no": "1168000000",
        "trade_types": ["B1"],
        "real_estate_types": ["APT"],
        "max_pages": 1,
        "criteria": {"max_deposit": 100000},
    }
    target.update(overrides)
    return target


def test_read_empty_config(client):
    body = client.get("/api/config").json()
    assert body["targets"] == []
    assert body["notify_on_first_run"] is False


def test_save_and_reload_config(client):
    payload = {"notify_on_first_run": True, "targets": [region_target()]}
    response = client.put("/api/config", json=payload)
    assert response.status_code == 200
    assert response.json() == {"saved": True, "targets": 1}

    # 파일로 저장되고, 다시 읽으면 같은 내용
    saved = WatchConfig.load(client.settings.config_path)
    assert saved.notify_on_first_run is True
    assert saved.targets[0].cortar_no == "1168000000"
    assert client.get("/api/config").json()["targets"][0]["name"] == "강남구 전세"


def test_save_rejects_invalid_target(client):
    payload = {"targets": [region_target(kind="complex", complex_no=None, cortar_no=None)]}
    assert client.put("/api/config", json=payload).status_code == 422
    # 잘못된 저장 시도로 기존 파일이 망가지지 않는다
    assert WatchConfig.load(client.settings.config_path).targets == []


def test_save_backs_up_previous_file(client):
    client.put("/api/config", json={"targets": [region_target(name="첫번째")]})
    client.put("/api/config", json={"targets": [region_target(name="두번째")]})

    backup = client.settings.config_path.with_suffix(".yaml.bak")
    assert "첫번째" in backup.read_text(encoding="utf-8")


def test_region_search(client):
    results = client.get("/api/regions/search", params={"q": "서울 강남구"}).json()
    assert results == [{"cortar_no": "1168000000", "path": "서울시 강남구"}]
    assert client.get("/api/regions/search", params={"q": "  "}).json() == []


def test_region_children(client):
    results = client.get("/api/regions/children", params={"cortar_no": "1168000000"}).json()
    assert results == [{"cortar_no": "1168010100", "name": "역삼동"}]


def test_preview_applies_criteria(client):
    body = client.post("/api/preview", json={"target": region_target()}).json()
    assert body["scopes"] == 1  # 강남구 → 역삼동 1곳
    assert body["fetched"] == 2
    assert body["matched"] == 1  # 15억짜리는 조건에서 탈락
    assert body["listings"][0]["name"] == "○○아파트"
    assert body["listings"][0]["url"].endswith("/articles/1")


def test_test_notify_without_telegram(client):
    response = client.post("/api/test-notify")
    assert response.status_code == 400
    assert "텔레그램" in response.json()["detail"]


def test_index_page_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "noti 설정" in response.text


def test_save_falls_back_when_rename_is_blocked(client, monkeypatch):
    """단일 파일 bind mount(도커)에서는 rename 이 EBUSY 로 실패한다."""
    from pathlib import Path

    def blocked_replace(self, target):
        raise OSError(16, "Device or resource busy")

    monkeypatch.setattr(Path, "replace", blocked_replace)
    response = client.put("/api/config", json={"targets": [region_target(name="덮어쓰기")]})

    assert response.status_code == 200
    assert "덮어쓰기" in client.settings.config_path.read_text(encoding="utf-8")
    assert not client.settings.config_path.with_suffix(".yaml.tmp").exists()


def test_cli_run_reports_missing_config(tmp_path, capsys, monkeypatch):
    """설정 파일이 없을 때 트레이스백 대신 안내를 출력한다."""
    from noti.cli import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NOTI_CONFIG_PATH", str(tmp_path / "없는파일.yaml"))
    assert main(["once", "--console"]) == 1
    assert "설정 파일이 없습니다" in capsys.readouterr().err
