"""토큰 없이 동작하는 모바일 클라이언트."""

import httpx
import pytest

from noti.config import Target
from noti.sources import NaverMobileClient

REGIONS = {
    # 실제 응답 필드명(CortarNo/CortarNm/MapXCrdn/MapYCrdn)
    "0000000000": [{"CortarNo": "1100000000", "CortarNm": "서울시", "MapYCrdn": "37.566427",
                    "MapXCrdn": "126.977872"}],
    "1100000000": [{"CortarNo": "1168000000", "CortarNm": "강남구", "MapYCrdn": "37.51",
                    "MapXCrdn": "127.04"}],
    "1168000000": [
        {"CortarNo": "1168010100", "CortarNm": "역삼동", "MapYCrdn": "37.499776",
         "MapXCrdn": "127.03895"},
        {"CortarNo": "1168010300", "CortarNm": "청담동", "MapYCrdn": "37.525492",
         "MapXCrdn": "127.05235"},
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
        if path == "/cluster/clusterList":
            return httpx.Response(
                200,
                json={"data": {"ARTICLE": [{"lgeo": "1101110", "count": 12, "lat": 37.5,
                                            "lon": 127.03}]}},
            )
        if path == "/cluster/ajax/articleList":
            # lgeo 없이 부르면 네이버가 null 을 준다
            if not request.url.params.get("lgeo"):
                return httpx.Response(200, json=None)
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


async def test_region_query_uses_cluster_then_articles():
    """clusterList 로 lgeo 를 얻은 뒤 그 클러스터의 매물을 가져온다."""
    requests: list = []
    client = make_client(requests)
    try:
        target = Target(name="역삼동", kind="region", cortar_no="1168010100", max_pages=1)
        await client.fetch_listings(target)
        paths = [u.path for u in requests]
        assert paths.index("/cluster/clusterList") < paths.index("/cluster/ajax/articleList")

        cluster_url = next(u for u in requests if u.path == "/cluster/clusterList")
        assert float(cluster_url.params["btm"]) < 37.499776 < float(cluster_url.params["top"])
        assert cluster_url.params["cortarNo"] == "1168010100"

        article_url = next(u for u in requests if u.path == "/cluster/ajax/articleList")
        assert article_url.params["lgeo"] == "1101110"
        assert article_url.params["totCnt"] == "12"
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


@pytest.mark.asyncio
async def test_null_payload_does_not_crash():
    """파라미터가 부족하면 네이버가 null 을 준다. 빈 결과로 넘어가야 한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/map/getRegionList":
            return httpx.Response(200, json={"result": {"list": REGIONS.get(
                request.url.params["cortarNo"], [])}})
        return httpx.Response(200, json=None)  # clusterList 가 null

    client = NaverMobileClient(request_delay=0)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="")
    try:
        target = Target(name="역삼동", kind="region", cortar_no="1168010100", max_pages=1)
        assert await client.fetch_listings(target) == []
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_dump_raw_compares_variants():
    """어떤 파라미터 조합이 데이터를 주는지 한 번에 비교할 수 있어야 한다."""
    client = make_client()
    try:
        samples = await client.dump_raw()
        labels = [s["label"] for s in samples]
        assert any("클러스터 A" in label for label in labels)
        assert any("클러스터 B" in label for label in labels)
        assert all("status" in s or "error" in s for s in samples)
    finally:
        await client.aclose()
