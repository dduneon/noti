"""fin.land.naver.com (현재 네이버 부동산 웹) 클라이언트.

데스크톱·모바일 모두 fin.land 로 연결되고, 지도 화면은 front-api 를 POST 로 호출한다.
예) POST /front-api/v1/article/map/articleClusters
    {"filter": {...}, "boundingBox": {"left","right","top","bottom"}, "precision": 15.4,
     "userChannelType": "PC"}

지역 코드·좌표는 아직 살아 있는 m.land 의 getRegionList 를 그대로 쓴다.
"""

from __future__ import annotations

import logging
from typing import Any, Self

import httpx

from ..config import Target
from ..models import Listing
from .naver import NaverLandError
from .naver_mobile import NaverMobileClient

logger = logging.getLogger(__name__)

BASE_URL = "https://fin.land.naver.com"
CLUSTERS_PATH = "/front-api/v1/article/map/articleClusters"
# 매물 목록 엔드포인트는 아직 확정하지 못했다. 후보를 차례로 시도하고 통한 것을 기억한다.
ARTICLE_LIST_PATHS = (
    "/front-api/v1/article/map/articles",
    "/front-api/v1/article/map/articleList",
    "/front-api/v1/article/list",
)

# 우리 설정(네이버 구코드) → fin.land 매물종류 코드. 잠정 매핑이라 바뀔 수 있다.
REAL_ESTATE_TYPES = {
    "APT": "A01",
    "OPST": "A02",
    "ABYG": "A03",
    "JGC": "A04",
    "VL": "B01",
    "DDDGG": "B02",
}
TRADE_TYPES = {"A1": "A1", "B1": "B1", "B2": "B2", "B3": "B3"}


def headers(page_url: str) -> dict[str, str]:
    """브라우저가 보내는 것과 같은 헤더. 인증 쿠키는 쓰지 않는다."""
    return {
        "accept": "application/json, text/plain, */*",
        "accept-language": "ko-KR,ko;q=0.9",
        "content-type": "application/json",
        "origin": BASE_URL,
        "referer": page_url,
        "x-page-url": page_url,
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
        ),
    }


def build_filter(target: Target) -> dict[str, Any]:
    return {
        "tradeTypes": [TRADE_TYPES.get(code, code) for code in target.trade_types],
        "realEstateTypes": [
            REAL_ESTATE_TYPES.get(code, code) for code in target.real_estate_types
        ],
        "roomCount": [],
        "bathRoomCount": [],
        "optionTypes": [],
        "oneRoomShapeTypes": [],
        "moveInTypes": [],
        "filtersExclusiveSpace": False,
        "floorTypes": [],
        "directionTypes": [],
        "hasArticlePhoto": False,
        "isAuthorizedByOwner": False,
        "parkingTypes": [],
        "entranceTypes": [],
        "hasArticle": False,
    }


def bounding_box(lat: float, lon: float, pad: float = 0.012) -> dict[str, float]:
    return {
        "left": round(lon - pad, 6),
        "right": round(lon + pad, 6),
        "top": round(lat + pad, 6),
        "bottom": round(lat - pad, 6),
    }


class NaverFinClient:
    """fin.land front-api 클라이언트. 지역 트리는 모바일 클라이언트를 재사용한다."""

    def __init__(self, *, timeout: float = 10.0, request_delay: float = 1.0) -> None:
        self._client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)
        self._regions = NaverMobileClient(timeout=timeout, request_delay=request_delay)
        self._request_delay = request_delay
        self._list_path_index = 0
        self.auth_token = None
        self.last_raw: dict[str, object] = {}

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()
        await self._regions.aclose()

    # --- 지역(모바일 엔드포인트 재사용) -------------------------------------

    async def fetch_regions(self, cortar_no: str) -> list[dict]:
        return await self._regions.fetch_regions(cortar_no)

    async def search_regions(self, query: str) -> list[tuple[str, str]]:
        return await self._regions.search_regions(query)

    async def resolve_scopes(self, target: Target) -> list[str]:
        return await self._regions.resolve_scopes(target)

    # --- front-api ----------------------------------------------------------

    async def _post(self, path: str, body: dict[str, Any], page_url: str) -> Any:
        response = await self._client.post(
            f"{BASE_URL}{path}", json=body, headers=headers(page_url)
        )
        self.last_raw = {
            "path": path,
            "body": body,
            "status": response.status_code,
            "response": response.text[:1500],
        }
        if response.status_code == 404:
            raise NaverLandError(f"{path} 가 존재하지 않습니다(404)")
        response.raise_for_status()
        try:
            return response.json()
        except ValueError as exc:
            raise NaverLandError(f"{path} 응답을 JSON 으로 파싱하지 못했습니다") from exc

    def _page_url(self, lat: float, lon: float) -> str:
        return f"{BASE_URL}/map?center={lat},{lon}&zoom=15"

    async def fetch_clusters(self, target: Target, lat: float, lon: float) -> list[dict]:
        payload = await self._post(
            CLUSTERS_PATH,
            {
                "filter": build_filter(target),
                "boundingBox": bounding_box(lat, lon),
                "precision": 15.4,
                "userChannelType": "PC",
            },
            self._page_url(lat, lon),
        )
        if not isinstance(payload, dict):
            return []
        data = payload.get("result") or payload.get("data") or payload
        clusters = data.get("clusters") or data.get("list") or data.get("articleClusters") or []
        return clusters if isinstance(clusters, list) else []

    async def fetch_listings(self, target: Target) -> list[Listing]:
        listings: list[Listing] = []
        seen: set[str] = set()

        for scope in await self.resolve_scopes(target):
            center = await self._regions._region_center(scope) if target.kind == "region" else None
            if target.kind == "region" and not center:
                logger.warning("지역 %s 의 좌표를 찾지 못해 건너뜁니다", scope)
                continue

            rows = await self._articles(target, center) if center else []
            for raw in rows:
                listing = Listing.from_mobile(raw)
                if not listing.article_no or listing.article_no in seen:
                    continue
                seen.add(listing.article_no)
                listings.append(listing)

        return listings

    async def _articles(self, target: Target, center: tuple[float, float]) -> list[dict]:
        """매물 목록. 엔드포인트 후보를 차례로 시도하고 통한 것을 기억한다."""
        lat, lon = center
        body = {
            "filter": build_filter(target),
            "boundingBox": bounding_box(lat, lon),
            "page": 1,
            "size": 100,
            "sort": "RANK",
            "userChannelType": "PC",
        }
        order = ARTICLE_LIST_PATHS[self._list_path_index :] + ARTICLE_LIST_PATHS[
            : self._list_path_index
        ]
        for path in order:
            try:
                payload = await self._post(path, body, self._page_url(lat, lon))
            except (NaverLandError, httpx.HTTPStatusError) as exc:
                logger.debug("%s 실패: %s", path, exc)
                continue

            rows = _extract_rows(payload)
            if rows:
                index = ARTICLE_LIST_PATHS.index(path)
                if index != self._list_path_index:
                    logger.info("매물 목록 엔드포인트를 %s 로 바꿉니다", path)
                    self._list_path_index = index
                return rows
        return []

    async def probe(self) -> dict[str, object]:
        result: dict[str, object] = {"source": "fin", "manual_token": False, "handshake": []}
        try:
            regions = await self.fetch_regions("0000000000")
            result["regions_sample"] = [r["cortarName"] for r in regions[:3]]
        except (NaverLandError, httpx.HTTPError) as exc:
            result["regions_error"] = f"{type(exc).__name__}: {exc}"
            return result

        target = Target(name="점검", kind="region", cortar_no="1168010100", max_pages=1)
        try:
            clusters = await self.fetch_clusters(target, 37.499776, 127.03895)
            result["clusters"] = len(clusters)
            listings = await self.fetch_listings(target)
            result["articles_count"] = len(listings)
            result["articles_sample"] = [x.summary() for x in listings[:2]]
        except (NaverLandError, httpx.HTTPError) as exc:
            result["articles_error"] = f"{type(exc).__name__}: {exc}"
        return result

    async def dump_raw(self) -> list[dict[str, object]]:
        """clusters 와 매물 목록 후보 엔드포인트의 응답 원문을 모은다."""
        lat, lon = 37.499776, 127.03895
        target = Target(name="점검", kind="region", cortar_no="1168010100", max_pages=1)
        samples: list[dict[str, object]] = []

        async def capture(label: str, path: str, body: dict[str, Any]) -> None:
            try:
                await self._post(path, body, self._page_url(lat, lon))
            except (NaverLandError, httpx.HTTPError, OSError) as exc:
                samples.append({"label": label, "error": str(exc), **self.last_raw})
            else:
                samples.append({"label": label, **self.last_raw})

        await capture(
            "클러스터",
            CLUSTERS_PATH,
            {
                "filter": build_filter(target),
                "boundingBox": bounding_box(lat, lon),
                "precision": 15.4,
                "userChannelType": "PC",
            },
        )
        list_body = {
            "filter": build_filter(target),
            "boundingBox": bounding_box(lat, lon),
            "page": 1,
            "size": 100,
            "sort": "RANK",
            "userChannelType": "PC",
        }
        for path in ARTICLE_LIST_PATHS:
            await capture(f"매물 목록 후보 {path}", path, list_body)
        return samples


def _extract_rows(payload: Any) -> list[dict]:
    """응답에서 매물 배열을 찾아낸다(키 이름이 확정되지 않아 몇 군데를 본다)."""
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for container in (payload.get("result"), payload.get("data"), payload):
        if isinstance(container, list):
            return [row for row in container if isinstance(row, dict)]
        if isinstance(container, dict):
            for key in ("articles", "list", "body", "articleList", "items"):
                rows = container.get(key)
                if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                    return rows
    return []
