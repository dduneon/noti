import pytest

from noti.config import Target
from noti.sources import NaverLandClient

# regions/list 가짜 응답: 서울 > 강남구 > (역삼동, 삼성동)
REGION_TREE = {
    "0000000000": [{"cortarNo": "1100000000", "cortarName": "서울시"}],
    "1100000000": [
        {"cortarNo": "1168000000", "cortarName": "강남구"},
        {"cortarNo": "1171000000", "cortarName": "송파구"},
    ],
    "1168000000": [
        {"cortarNo": "1168010100", "cortarName": "역삼동"},
        {"cortarNo": "1168010500", "cortarName": "삼성동"},
    ],
    "1168010100": [],
}


@pytest.fixture
def client(monkeypatch):
    client = NaverLandClient(request_delay=0)

    async def fake_get_json(path, params, **kwargs):
        if path == "/api/regions/list":
            return {"regionList": REGION_TREE.get(str(params["cortarNo"]), [])}
        return {"articleList": [], "isMoreData": False}

    monkeypatch.setattr(client, "_get_json", fake_get_json)
    return client


async def test_region_target_expands_to_dongs(client):
    target = Target(name="강남구", kind="region", cortar_no="1168000000")
    assert await client.resolve_scopes(target) == ["1168010100", "1168010500"]


async def test_leaf_region_used_as_is(client):
    target = Target(name="역삼동", kind="region", cortar_no="1168010100")
    assert await client.resolve_scopes(target) == ["1168010100"]


async def test_expand_can_be_disabled(client):
    target = Target(
        name="강남구", kind="region", cortar_no="1168000000", expand_subregions=False
    )
    assert await client.resolve_scopes(target) == ["1168000000"]


async def test_subregion_cap(client):
    target = Target(name="강남구", kind="region", cortar_no="1168000000", max_subregions=1)
    assert await client.resolve_scopes(target) == ["1168010100"]


async def test_complex_target_scope(client):
    target = Target(name="단지", kind="complex", complex_no="111515")
    assert await client.resolve_scopes(target) == ["111515"]


async def test_search_regions_by_name(client):
    assert await client.search_regions("서울 강남구 역삼동") == [("서울시 강남구 역삼동", "1168010100")]
    assert await client.search_regions("서울 강남구") == [("서울시 강남구", "1168000000")]
    assert await client.search_regions("서울 없는구") == []
