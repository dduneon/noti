"""토큰 없이 동작하는 모바일 클라이언트."""

import httpx
import pytest

from noti.config import Target
from noti.sources import NaverMobileClient

REGIONS = {
    "0000000000": [{"cortarNo": "1100000000", "cortarNm": "서울시", "lat": 37.56, "lon": 126.97}],
    "1100000000": [{"cortarNo": "1168000000", "cortarNm": "강남구", "lat": 37.51, "lon": 127.04}],
    "1168000000": [
        {"cortarNo": "1168010100", "cortarNm": "역삼동", "lat": 37.50, "lon": 127.03},
        {"cortarNo": "1168010300", "cortarNm": "청담동", "lat": 37.52, "lon": 127.05},
    ],
    "1168010100": [],
}

REGION_ARTICLES = [
    {"atclNo": "1", "atclNm": "○○아파트", "tradTpNm": "전세", "prc": 80000, "hanPrc": "8억",
     "spc1": 112.0, "spc2": 84.97, "flrInfo": "7/15", "direction": "남향", "rltrNm": "○○공인"},
]
COMPLEX_ARTICLES = [
    {"atclNo": "9", "atclNm": "래미안", "tradTpNm": "월세", "prc": 1000, "rentPrc": 130,
     "spc2": 59.9, "flrInfo": "고/15"},
]


def make_client(recorder: list | None = None) -> NaverMobileClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if recorder is not None:
            recorder.append(request.url)
        path = request.url.path
        if path == "/map/getRegionList":
            cortar_no = request.url.params["cortarNo"]
            return httpx.Response(200, json={"result": {"list": REGIONS.get(cortar_no, [])}})
        if path == "/cluster/ajax/articleList":
            return httpx.Response(200, json={"body": REGION_ARTICLES, "more": False})
        if path == "/complex/getComplexArticleList":
            return httpx.Response(
                200, json={"result": {"list": COMPLEX_ARTICLES, "moreDataYn": "N"}}
            )
        return httpx.Response(404)

    client = NaverMobileClient(request_delay=0)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="")
    return client


async def test_region_listings_need_no_token():
    """Authorization 헤더 없이 매물이 조회된다."""
    requests: list = []
    client = make_client(requests)
    try:
        target = Target(name="역삼동", kind="region", cortar_no="1168010100", max_pages=1)
        listings = await client.fetch_listings(target)
        assert [x.article_no for x in listings] == ["1"]
        assert listings[0].deposit == 80000 and listings[0].area_m2 == 84.97
        assert client.auth_token is None
    finally:
        await client.aclose()


async def test_region_query_carries_map_bounds():
    """지도 기반 API 라 동 중심 좌표에서 범위를 만들어 넘긴다."""
    requests: list = []
    client = make_client(requests)
    try:
        target = Target(name="역삼동", kind="region", cortar_no="1168010100", max_pages=1)
        await client.fetch_listings(target)
        article_url = next(u for u in requests if u.path == "/cluster/ajax/articleList")
        params = article_url.params
        assert float(params["btm"]) < 37.50 < float(params["top"])
        assert float(params["lft"]) < 127.03 < float(params["rgt"])
        assert params["cortarNo"] == "1168010100"
    finally:
        await client.aclose()


async def test_complex_listings():
    client = make_client()
    try:
        target = Target(name="단지", kind="complex", complex_no="111515", max_pages=1)
        listings = await client.fetch_listings(target)
        assert listings[0].monthly == 130
        assert listings[0].complex_no == "111515"
        assert listings[0].floor is None  # '고/15' 는 층을 못 읽는다
    finally:
        await client.aclose()


async def test_region_expansion_and_search():
    client = make_client()
    try:
        target = Target(name="강남구", kind="region", cortar_no="1168000000")
        assert await client.resolve_scopes(target) == ["1168010100", "1168010300"]
        assert await client.search_regions("서울 강남구 역삼동") == [
            ("서울시 강남구 역삼동", "1168010100")
        ]
    finally:
        await client.aclose()


async def test_probe_reports_listings():
    client = make_client()
    try:
        result = await client.probe()
        assert result["source"] == "mobile"
        assert result["articles_count"] == 1
        assert result.get("articles_error") is None
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_error_code_is_reported():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "fail", "message": "bad request"})

    client = NaverMobileClient(request_delay=0)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="")
    try:
        from noti.sources import NaverLandError

        with pytest.raises(NaverLandError):
            await client.fetch_regions("0000000000")
    finally:
        await client.aclose()
