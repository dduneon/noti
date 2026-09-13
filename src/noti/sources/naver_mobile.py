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
from ..models import Listing, pick
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
ZOOM = 14
MAX_CLUSTERS = 10  # 한 지역에서 조회할 클러스터 수 상한(요청 폭주 방지)


def _bounds(lat: float, lon: float) -> dict[str, float]:
    return {
        "btm": round(lat - BBOX_PAD, 6),
        "top": round(lat + BBOX_PAD, 6),
        "lft": round(lon - BBOX_PAD, 6),
        "rgt": round(lon + BBOX_PAD, 6),
    }


class NaverMobileClient:
    """토큰 없이 매물을 조회하는 클라이언트."""

    def __init__(self, *, timeout: float = 10.0, request_delay: float = 1.0) -> None:
        self._client = httpx.AsyncClient(
            timeout=timeout, headers=dict(MOBILE_HEADERS), follow_redirects=True
        )
        self._request_delay = request_delay
        self._region_cache: dict[str, list[dict]] = {}
        self.auth_token = None  # 인터페이스 호환용(모바일은 토큰이 필요 없다)
        self.last_raw: dict[str, object] = {}  # doctor --raw 용 마지막 응답

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get_json(
        self, path: str, params: dict[str, str | int], *, allow_non_json: bool = False
    ) -> dict:
        response = await self._client.get(f"{BASE_URL}{path}", params=params)
        response.raise_for_status()
        self.last_raw = {"path": path, "params": dict(params), "body": response.text[:1500]}
        try:
            payload = response.json()
        except ValueError as exc:
            if allow_non_json:
                # 더 이상 하위 지역이 없는 동을 조회하면 JSON 이 아닌 응답이 오기도 한다.
                logger.debug("%s 응답이 JSON 이 아닙니다(무시): %s", path, response.text[:120])
                return {}
            raise NaverLandError(f"{path} 응답을 JSON 으로 파싱하지 못했습니다") from exc

        if payload is None:
            # 파라미터가 부족하면 네이버가 본문 없이 null 을 준다. 빈 결과로 취급한다.
            logger.debug("%s 가 null 을 반환했습니다 (params=%s)", path, params)
            return {}
        if isinstance(payload, list):
            return {"body": payload}
        if not isinstance(payload, dict):
            return {}
        if payload.get("code") not in (None, "success", 200, "200"):
            raise NaverLandError(f"{path} 응답 코드가 정상이 아닙니다: {payload.get('code')}")
        return payload

    # --- 지역 ---------------------------------------------------------------

    async def fetch_regions(self, cortar_no: str) -> list[dict]:
        """하위 지역 목록. 데스크톱 응답과 같은 모양으로 맞춰 돌려준다."""
        payload = await self._get_json(
            "/map/getRegionList", {"cortarNo": cortar_no}, allow_non_json=True
        )
        regions = (
            (payload.get("result") or {}).get("list")
            or payload.get("regionList")
            or payload.get("list")
            or []
        )
        normalized = []
        for region in regions:
            code = pick(region, "cortarNo", "CortarNo", "cortarNO", "cortar_no")
            if not code:
                continue
            normalized.append(
                {
                    "cortarNo": str(code),
                    "cortarName": str(
                        pick(region, "cortarNm", "cortarName", "CortarNm", "name", "cortarNMs")
                        or ""
                    ),
                    # 모바일 응답은 MapYCrdn=위도, MapXCrdn=경도 로 준다.
                    "lat": pick(region, "MapYCrdn", "lat", "centerLat", "cortarLat", "y"),
                    "lon": pick(region, "MapXCrdn", "lon", "centerLon", "cortarLon", "lng", "x"),
                }
            )
        return normalized

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
        """지역 매물. 지도 클러스터를 먼저 얻고(lgeo) 그 안의 매물을 가져온다.

        articleList 는 lgeo 없이 부르면 null 을 돌려준다. 그래서 clusterList 로
        해당 범위의 클러스터 목록을 받은 뒤, 클러스터마다 매물을 조회한다.
        """
        if page > 1:
            return [], False  # 페이지 순회는 클러스터 단위로 이 안에서 처리한다

        center = await self._region_center(cortar_no)
        if not center:
            raise NaverLandError(f"지역 {cortar_no} 의 좌표를 찾지 못했습니다")

        bounds = _bounds(*center)
        clusters = await self._cluster_list(cortar_no, target, center, bounds)
        if not clusters:
            return [], False

        rows: list[dict] = []
        for index, cluster in enumerate(clusters[:MAX_CLUSTERS]):
            if index:
                await asyncio.sleep(self._request_delay)
            rows.extend(await self._cluster_articles(cluster, target, bounds))
        if len(clusters) > MAX_CLUSTERS:
            logger.warning(
                "지역 %s 의 클러스터가 %d개라 앞에서 %d개만 조회합니다",
                cortar_no,
                len(clusters),
                MAX_CLUSTERS,
            )
        return rows, False

    async def _cluster_list(
        self,
        cortar_no: str,
        target: Target,
        center: tuple[float, float],
        bounds: dict[str, float],
    ) -> list[dict]:
        lat, lon = center
        payload = await self._get_json(
            "/cluster/clusterList",
            {
                "view": "atcl",
                "cortarNo": cortar_no,
                "rletTpCd": ":".join(target.real_estate_types),
                "tradTpCd": ":".join(target.trade_types),
                "z": ZOOM,
                "lat": lat,
                "lon": lon,
                **bounds,
                "pCortarNo": "",
                "addon": "COMPLEX",
                "bAddon": "COMPLEX",
                "isOnlyIsale": "false",
            },
            allow_non_json=True,
        )
        data = payload.get("data") or {}
        clusters = data.get("ARTICLE") or data.get("COMPLEX") or []
        return [cluster for cluster in clusters if cluster.get("lgeo")]

    async def _cluster_articles(
        self, cluster: dict, target: Target, bounds: dict[str, float]
    ) -> list[dict]:
        rows: list[dict] = []
        for page in range(1, target.max_pages + 1):
            if page > 1:
                await asyncio.sleep(self._request_delay)
            payload = await self._get_json(
                "/cluster/ajax/articleList",
                {
                    "itemId": cluster.get("lgeo", ""),
                    "mapKey": "",
                    "lgeo": cluster.get("lgeo", ""),
                    "showR0": "",
                    "rletTpCd": ":".join(target.real_estate_types),
                    "tradTpCd": ":".join(target.trade_types),
                    "z": ZOOM,
                    "lat": cluster.get("lat", 0),
                    "lon": cluster.get("lon", 0),
                    **bounds,
                    "totCnt": cluster.get("count", 0),
                    "cortarNo": "",
                    "sort": "rank",
                    "page": page,
                },
                allow_non_json=True,
            )
            body = payload.get("body") or []
            rows.extend(body)
            if not payload.get("more"):
                break
        return rows

    async def _region_center(self, cortar_no: str) -> tuple[float, float] | None:
        """동 자신의 좌표는 부모 지역 목록에 들어 있다(구 코드로 조회)."""
        parent = cortar_no[:5] + "00000"
        for candidate in (parent, cortar_no):
            for region in await self._subregions(candidate):
                if region["cortarNo"] == cortar_no and region.get("lat") and region.get("lon"):
                    return float(region["lat"]), float(region["lon"])
        return None

    async def dump_raw(self) -> list[dict[str, object]]:
        """응답 원문을 그대로 모아 돌려준다(doctor --raw).

        클러스터 API 는 파라미터 조합에 민감해 null 을 주기 쉽다. 그래서 몇 가지
        변형을 한 번에 호출해 어느 조합이 데이터를 주는지 비교할 수 있게 한다.
        """
        lat, lon = 37.499776, 127.03895  # 역삼동
        bounds = _bounds(lat, lon)
        map_referer = {"Referer": f"{BASE_URL}/map/{lat}:{lon}:{ZOOM}"}
        base = {
            "view": "atcl",
            "rletTpCd": "APT",
            "tradTpCd": "A1",
            "lat": lat,
            "lon": lon,
            **bounds,
            "pCortarNo": "",
            "addon": "COMPLEX",
            "bAddon": "COMPLEX",
            "isOnlyIsale": "false",
        }

        samples = [
            await self._raw_get("지역 목록", "/map/getRegionList", {"cortarNo": "1168000000"}),
            await self._raw_get(
                "클러스터 A (z=14, cortarNo 있음)",
                "/cluster/clusterList",
                {**base, "z": 14, "cortarNo": "1168010100"},
            ),
            await self._raw_get(
                "클러스터 B (z=13, cortarNo 없음)",
                "/cluster/clusterList",
                {**base, "z": 13, "cortarNo": ""},
            ),
            await self._raw_get(
                "클러스터 C (지도 Referer)",
                "/cluster/clusterList",
                {**base, "z": 13, "cortarNo": "1168010100"},
                headers=map_referer,
            ),
            await self._raw_get(
                "단지 매물 (hscpNo=111515)",
                "/complex/getComplexArticleList",
                {"hscpNo": "111515", "tradTpCd": "A1", "order": "point_", "page": 1},
            ),
        ]
        return samples

    async def _raw_get(
        self,
        label: str,
        path: str,
        params: dict[str, str | int],
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        try:
            response = await self._client.get(
                f"{BASE_URL}{path}", params=params, headers=headers
            )
            return {
                "label": label,
                "path": path,
                "params": params,
                "status": response.status_code,
                "body": response.text[:1200],
            }
        except (httpx.HTTPError, OSError) as exc:
            return {"label": label, "path": path, "params": params, "error": str(exc)}

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
        center = await self._region_center("1168010100")
        try:
            # 클러스터 단계와 매물 단계를 나눠서 어디가 비었는지 알 수 있게 한다.
            clusters = (
                await self._cluster_list("1168010100", target, center, _bounds(*center))
                if center
                else []
            )
            result["clusters"] = len(clusters)
            listings = await self.fetch_listings(target)
            result["articles_count"] = len(listings)
            result["articles_sample"] = [x.summary() for x in listings[:2]]
        except (NaverLandError, httpx.HTTPError) as exc:
            result["articles_error"] = f"{type(exc).__name__}: {exc}"
        return result
