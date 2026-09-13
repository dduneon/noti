"""m.land.naver.com (모바일 웹) 클라이언트.

데스크톱(new.land)의 매물 API 는 페이지 JS 가 만드는 Authorization JWT 를 요구하지만,
모바일 웹이 쓰는 엔드포인트는 토큰 없이 Referer 만으로 동작한다. 그래서 이쪽을 기본으로 쓴다.
공식 API 가 아니므로 응답 필드 이름이 바뀔 수 있어, 매퍼는 후보 키를 여러 개 본다.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Self

import httpx

from ..config import Target
from ..models import Listing
from .naver import NaverLandError

logger = logging.getLogger(__name__)

BASE_URL = "https://m.land.naver.com"
ROOT_CORTAR_NO = "0000000000"
MOBILE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Referer": f"{BASE_URL}/",
    "X-Requested-With": "XMLHttpRequest",
}
# 동 중심 좌표에서 지도 범위를 만들 때 쓰는 여유(도 단위). 약 2~3km.
BBOX_PAD = 0.025


class NaverMobileClient:
    """토큰 없이 매물을 조회하는 클라이언트."""

    def __init__(self, *, timeout: float = 10.0, request_delay: float = 1.0) -> None:
        self._client = httpx.AsyncClient(
            timeout=timeout, headers=dict(MOBILE_HEADERS), follow_redirects=True
        )
        self._request_delay = request_delay
        self._region_cache: dict[str, list[dict]] = {}
        self.auth_token = None  # 인터페이스 호환용(모바일은 토큰이 필요 없다)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get_json(self, path: str, params: dict[str, str | int]) -> dict:
        response = await self._client.get(f"{BASE_URL}{path}", params=params)
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise NaverLandError(f"{path} 응답을 JSON 으로 파싱하지 못했습니다") from exc

        if isinstance(payload, dict) and payload.get("code") not in (None, "success", 200, "200"):
            raise NaverLandError(f"{path} 응답 코드가 정상이 아닙니다: {payload.get('code')}")
        return payload

    # --- 지역 ---------------------------------------------------------------

    async def fetch_regions(self, cortar_no: str) -> list[dict]:
        """하위 지역 목록. 데스크톱 응답과 같은 모양으로 맞춰 돌려준다."""
        payload = await self._get_json("/map/getRegionList", {"cortarNo": cortar_no})
        regions = (payload.get("result") or {}).get("list") or payload.get("regionList") or []
        return [
            {
                "cortarNo": str(region.get("cortarNo") or region.get("CortarNo") or ""),
                "cortarName": region.get("cortarNm") or region.get("cortarName") or "",
                "lat": region.get("lat") or region.get("centerLat"),
                "lon": region.get("lon") or region.get("centerLon"),
            }
            for region in regions
            if region.get("cortarNo") or region.get("CortarNo")
        ]

    async def _subregions(self, cortar_no: str) -> list[dict]:
        if cortar_no not in self._region_cache:
            self._region_cache[cortar_no] = await self.fetch_regions(cortar_no)
        return self._region_cache[cortar_no]

    async def search_regions(self, query: str) -> list[tuple[str, str]]:
        frontier: list[tuple[str, str]] = [("", ROOT_CORTAR_NO)]
        for token in query.split():
            matches: list[tuple[str, str]] = []
            for path, code in frontier:
                for region in await self._subregions(code):
                    name = str(region.get("cortarName") or "")
                    if token in name:
                        matches.append((f"{path} {name}".strip(), str(region["cortarNo"])))
            if not matches:
                return []
            frontier = matches
        return frontier

    async def resolve_scopes(self, target: Target) -> list[str]:
        if target.kind == "complex":
            assert target.complex_no
            return [target.complex_no]

        assert target.cortar_no
        if not target.expand_subregions:
            return [target.cortar_no]

        children = await self._subregions(target.cortar_no)
        if not children:
            return [target.cortar_no]

        codes = [region["cortarNo"] for region in children if region["cortarNo"]]
        if len(codes) > target.max_subregions:
            logger.warning(
                "target=%s 하위 지역이 %d개라 앞에서 %d개만 감시합니다",
                target.name,
                len(codes),
                target.max_subregions,
            )
            codes = codes[: target.max_subregions]
        logger.info("target=%s 지역 %d개로 펼침", target.name, len(codes))
        return codes

    # --- 매물 ---------------------------------------------------------------

    async def fetch_listings(self, target: Target) -> list[Listing]:
        listings: list[Listing] = []
        seen: set[str] = set()
        first_request = True

        for scope in await self.resolve_scopes(target):
            for page in range(1, target.max_pages + 1):
                if not first_request:
                    await asyncio.sleep(self._request_delay)
                first_request = False

                rows, has_more = await self._fetch_page(target, scope, page)
                for raw in rows:
                    listing = Listing.from_mobile(
                        raw, complex_no=scope if target.kind == "complex" else None
                    )
                    if not listing.article_no or listing.article_no in seen:
                        continue
                    seen.add(listing.article_no)
                    listings.append(listing)

                if not has_more:
                    break

        logger.debug("target=%s 매물 %d건 조회", target.name, len(listings))
        return listings

    async def _fetch_page(
        self, target: Target, scope: str, page: int
    ) -> tuple[list[dict], bool]:
        if target.kind == "complex":
            return await self._fetch_complex_page(scope, target, page)
        return await self._fetch_region_page(scope, target, page)

    async def _fetch_complex_page(
        self, complex_no: str, target: Target, page: int
    ) -> tuple[list[dict], bool]:
        payload = await self._get_json(
            "/complex/getComplexArticleList",
            {
                "hscpNo": complex_no,
                "tradTpCd": ":".join(target.trade_types),
                "order": "point_",
                "showR0": "",
                "page": page,
            },
        )
        result = payload.get("result") or {}
        rows = result.get("list") or []
        return rows, bool(result.get("moreDataYn") in ("Y", True) or result.get("more"))

    async def _fetch_region_page(
        self, cortar_no: str, target: Target, page: int
    ) -> tuple[list[dict], bool]:
        """지역 매물. 지도 기반 API 라 동 중심 좌표로 범위를 만들어 넘긴다."""
        center = await self._region_center(cortar_no)
        if not center:
            raise NaverLandError(f"지역 {cortar_no} 의 좌표를 찾지 못했습니다")

        lat, lon = center
        payload = await self._get_json(
            "/cluster/ajax/articleList",
            {
                "cortarNo": cortar_no,
                "rletTpCd": ":".join(target.real_estate_types),
                "tradTpCd": ":".join(target.trade_types),
                "z": 14,
                "lat": lat,
                "lon": lon,
                "btm": round(lat - BBOX_PAD, 6),
                "top": round(lat + BBOX_PAD, 6),
                "lft": round(lon - BBOX_PAD, 6),
                "rgt": round(lon + BBOX_PAD, 6),
                "page": page,
                "showR0": "",
            },
        )
        rows = payload.get("body") or (payload.get("result") or {}).get("list") or []
        return rows, bool(payload.get("more"))

    async def _region_center(self, cortar_no: str) -> tuple[float, float] | None:
        """동 자신의 좌표는 부모 지역 목록에 들어 있다(구 코드로 조회)."""
        parent = cortar_no[:5] + "00000"
        for candidate in (parent, cortar_no):
            for region in await self._subregions(candidate):
                if region["cortarNo"] == cortar_no and region.get("lat") and region.get("lon"):
                    return float(region["lat"]), float(region["lon"])
        return None

    async def probe(self) -> dict[str, object]:
        """noti doctor 용 점검(토큰이 필요 없으므로 조회만 확인한다)."""
        result: dict[str, object] = {"source": "mobile", "manual_token": False, "handshake": []}
        try:
            regions = await self.fetch_regions(ROOT_CORTAR_NO)
            result["regions_sample"] = [r["cortarName"] for r in regions[:3]]
        except (NaverLandError, httpx.HTTPError) as exc:
            result["regions_error"] = f"{type(exc).__name__}: {exc}"
            return result

        target = Target(name="점검", kind="region", cortar_no="1168010100", max_pages=1)
        try:
            listings = await self.fetch_listings(target)
            result["articles_count"] = len(listings)
            result["articles_sample"] = [x.summary() for x in listings[:2]]
        except (NaverLandError, httpx.HTTPError) as exc:
            result["articles_error"] = f"{type(exc).__name__}: {exc}"
        return result
