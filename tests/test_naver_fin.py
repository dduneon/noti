"""fin.land front-api 클라이언트."""

import json

import httpx
import pytest

from noti.config import Target
from noti.sources import NaverFinClient
from noti.sources.naver_fin import ARTICLE_LIST_PATHS, CLUSTERS_PATH, build_filter

REGIONS = {
    "1168000000": [
        {"CortarNo": "1168010100", "CortarNm": "역삼동", "MapYCrdn": "37.499776",
         "MapXCrdn": "127.03895"},
    ],
    "1168010100": [],
}
ARTICLES = [
    {"atclNo": "1", "atclNm": "○○아파트", "tradTpNm": "전세", "prc": 80000, "hanPrc": "8억",
     "spc2": 84.97, "flrInfo": "7/15"},
]


def make_client(working_list_path: str | None = None, recorder: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if recorder is not None:
            recorder.append(request)
        if request.url.host == "m.land.naver.com":
            cortar_no = request.url.params["cortarNo"]
            return httpx.Response(200, json={"result": {"list": REGIONS.get(cortar_no, [])}})
        if request.url.path == CLUSTERS_PATH:
            return httpx.Response(200, json={"result": {"clusters": [{"count": 12}]}})
        if working_list_path and request.url.path == working_list_path:
            return httpx.Response(200, json={"result": {"articles": ARTICLES}})
        return httpx.Response(404, json={"error": "not found"})

    client = NaverFinClient(request_delay=0)
    transport = httpx.MockTransport(handler)
    client._client = httpx.AsyncClient(transport=transport, base_url="")
    client._regions._client = httpx.AsyncClient(transport=transport, base_url="")
    return client


async def test_cluster_request_matches_browser_shape():
    """브라우저가 보내는 POST 본문 모양 그대로 보낸다."""
    requests: list = []
    client = make_client(recorder=requests)
    try:
        target = Target(
            name="역삼동", kind="region", cortar_no="1168010100",
            trade_types=["A1", "B1"], real_estate_types=["APT", "OPST"],
        )
        clusters = await client.fetch_clusters(target, 37.499776, 127.03895)
        assert clusters == [{"count": 12}]

        call = next(r for r in requests if r.url.path == CLUSTERS_PATH)
        assert call.method == "POST"
        body = json.loads(call.content)
        assert body["filter"]["tradeTypes"] == ["A1", "B1"]
        assert body["filter"]["realEstateTypes"] == ["A01", "A02"]  # 구코드 → fin 코드
        assert set(body["boundingBox"]) == {"left", "right", "top", "bottom"}
        assert body["userChannelType"] == "PC"
        assert call.headers["x-page-url"].startswith("https://fin.land.naver.com/map")
        assert "cookie" not in call.headers  # 로그인 쿠키는 보내지 않는다
    finally:
        await client.aclose()


async def test_finds_working_article_endpoint_and_remembers_it():
    """후보 경로를 차례로 시도하고 통한 것을 기억한다."""
    working = ARTICLE_LIST_PATHS[1]
    requests: list = []
    client = make_client(working_list_path=working, recorder=requests)
    try:
        target = Target(name="역삼동", kind="region", cortar_no="1168010100")
        listings = await client.fetch_listings(target)
        assert [x.article_no for x in listings] == ["1"]
        assert client._list_path_index == 1

        requests.clear()
        await client.fetch_listings(target)
        first_api_call = next(r for r in requests if r.url.host == "fin.land.naver.com")
        assert first_api_call.url.path == working  # 두 번째부터는 바로 그 경로로
    finally:
        await client.aclose()


async def test_no_listings_when_every_candidate_fails():
    client = make_client(working_list_path=None)
    try:
        target = Target(name="역삼동", kind="region", cortar_no="1168010100")
        assert await client.fetch_listings(target) == []
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_probe_and_dump_raw():
    client = make_client(working_list_path=ARTICLE_LIST_PATHS[0])
    try:
        result = await client.probe()
        assert result["source"] == "fin"
        assert result["clusters"] == 1
        assert result["articles_count"] == 1

        samples = await client.dump_raw()
        labels = [s["label"] for s in samples]
        assert "클러스터" in labels
        assert len(samples) == 1 + len(ARTICLE_LIST_PATHS)
    finally:
        await client.aclose()


def test_filter_defaults_are_complete():
    """네이버가 보내는 필드를 빠짐없이 채운다(누락되면 400 이 난다)."""
    body = build_filter(Target(name="t", kind="region", cortar_no="1168010100"))
    for key in ("roomCount", "optionTypes", "floorTypes", "directionTypes", "hasArticle"):
        assert key in body
