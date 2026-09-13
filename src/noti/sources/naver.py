"""네이버 부동산(new.land.naver.com) 비공식 API 클라이언트.

공개 문서가 없는 내부 API라서 응답 스키마나 인증 방식이 예고 없이 바뀔 수 있다.
개인용 모니터링 수준의 저빈도 호출을 전제로 하고, 호출 간 간격을 항상 둔다.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Self

import httpx

from ..config import Target
from ..models import Listing

logger = logging.getLogger(__name__)

BASE_URL = "https://new.land.naver.com"
# 토큰 쿠키를 받기 위해 차례로 시도할 경로. 경로 하나가 막히거나 404 로 리다이렉트돼도
# 다음 경로로 넘어간다(네이버가 라우팅을 자주 바꾼다).
BOOTSTRAP_PATHS = ("/", "/complexes", "/houses", "/offices", "/villas")
TOKEN_COOKIE = "REALESTATE"
ROOT_CORTAR_NO = "0000000000"  # 시/도 목록의 부모 코드
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)
# 브라우저와 비슷하게 보내야 봇으로 걸러지지 않는다.
BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Referer": f"{BASE_URL}/",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}


def _describe(attempt: dict[str, object]) -> str:
    if "error" in attempt:
        return f"{attempt['path']}(오류: {attempt['error']})"
    return f"{attempt['path']}→{attempt['status']} {attempt['final_url']}"


class NaverLandError(RuntimeError):
    """네이버 응답이 실패했거나 예상과 다를 때."""


class NaverLandClient:
    """매물 목록 조회 클라이언트.

    인증: new.land.naver.com 에 한 번 접속하면 `REALESTATE` 쿠키(JWT)가 내려오고,
    API 호출 시 이 값을 `Authorization: Bearer <jwt>` 로 넣어야 한다.
    토큰이 만료되면 401/403 이 오므로 한 번 재발급 후 재시도한다.
    """

    def __init__(self, *, timeout: float = 10.0, request_delay: float = 1.0) -> None:
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers=dict(BROWSER_HEADERS),
            follow_redirects=True,
        )
        self._request_delay = request_delay
        self._token: str | None = None
        self._region_cache: dict[str, list[dict]] = {}
        self.last_handshake: list[dict[str, object]] = []  # noti doctor 용 기록

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _ensure_token(self, *, force: bool = False) -> str:
        """토큰 쿠키를 받아온다. 경로를 하나씩 시도하고 결과를 기록해 둔다."""
        if self._token and not force:
            return self._token

        self.last_handshake = []
        for path in BOOTSTRAP_PATHS:
            try:
                response = await self._client.get(f"{BASE_URL}{path}")
            except httpx.HTTPError as exc:
                self.last_handshake.append({"path": path, "error": str(exc)})
                continue

            token = self._client.cookies.get(TOKEN_COOKIE)
            self.last_handshake.append(
                {
                    "path": path,
                    "status": response.status_code,
                    "final_url": str(response.url),
                    "cookies": sorted({cookie.name for cookie in self._client.cookies.jar}),
                    "got_token": bool(token),
                }
            )
            if token:
                self._token = token
                logger.debug("토큰 발급 성공 (%s)", path)
                return token

        raise NaverLandError(
            f"{TOKEN_COOKIE} 토큰 쿠키를 받지 못했습니다. 시도한 경로: "
            + ", ".join(_describe(attempt) for attempt in self.last_handshake)
            + " — `noti doctor` 로 자세히 볼 수 있습니다."
        )

    async def _get_json(self, path: str, params: dict[str, str | int]) -> dict:
        token = await self._ensure_token()
        headers = {"Authorization": f"Bearer {token}"}
        response = await self._client.get(f"{BASE_URL}{path}", params=params, headers=headers)

        if response.status_code in (401, 403):
            logger.info("토큰이 만료된 것 같아 재발급합니다 (status=%s)", response.status_code)
            token = await self._ensure_token(force=True)
            response = await self._client.get(
                f"{BASE_URL}{path}", params=params, headers={"Authorization": f"Bearer {token}"}
            )

        response.raise_for_status()
        try:
            return response.json()
        except ValueError as exc:  # HTML 에러 페이지가 오는 경우
            raise NaverLandError(f"{path} 응답을 JSON 으로 파싱하지 못했습니다") from exc

    async def fetch_listings(self, target: Target) -> list[Listing]:
        """대상(단지/지역)의 매물을 모아서 반환한다.

        지역 감시는 구/시 코드를 주면 하위 동으로 펼친 뒤 동별로 조회한다.
        (네이버 매물 목록 API 는 동 단위 cortarNo 를 기대한다.)
        """
        listings: list[Listing] = []
        seen: set[str] = set()
        first_request = True

        for scope in await self.resolve_scopes(target):
            for page in range(1, target.max_pages + 1):
                if not first_request:
                    await asyncio.sleep(self._request_delay)
                first_request = False

                payload = await self._fetch_page(target, scope, page)
                for raw in payload.get("articleList") or []:
                    listing = Listing.from_api(raw)
                    if listing.article_no in seen:
                        continue
                    seen.add(listing.article_no)
                    listings.append(listing)

                if not payload.get("isMoreData"):
                    break

        logger.debug("target=%s 매물 %d건 조회", target.name, len(listings))
        return listings

    async def resolve_scopes(self, target: Target) -> list[str]:
        """조회 단위 목록. 단지는 complexNo 하나, 지역은 동 코드 목록."""
        if target.kind == "complex":
            assert target.complex_no
            return [target.complex_no]

        assert target.cortar_no
        if not target.expand_subregions:
            return [target.cortar_no]

        children = await self._subregions(target.cortar_no)
        if not children:  # 이미 동 단위라 하위 지역이 없다
            return [target.cortar_no]

        codes = [str(region["cortarNo"]) for region in children if region.get("cortarNo")]
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

    async def _subregions(self, cortar_no: str) -> list[dict]:
        """하위 지역 목록(캐시). 같은 사이클에서 반복 호출해도 요청은 한 번."""
        if cortar_no not in self._region_cache:
            self._region_cache[cortar_no] = await self.fetch_regions(cortar_no)
        return self._region_cache[cortar_no]

    async def _fetch_page(self, target: Target, scope: str, page: int) -> dict:
        params: dict[str, str | int] = {
            "realEstateType": ":".join(target.real_estate_types),
            "tradeType": ":".join(target.trade_types),
            "page": page,
            "order": "rank",
            "priceType": "RETAIL",
            "articleState": "",
        }

        if target.kind == "complex":
            params["complexNo"] = scope
            return await self._get_json(f"/api/articles/complex/{scope}", params)

        params["cortarNo"] = scope
        return await self._get_json("/api/articles", params)

    async def search_regions(self, query: str) -> list[tuple[str, str]]:
        """'서울 강남구 역삼동' 처럼 띄어쓴 이름으로 지역 코드를 찾는다.

        토큰을 하나씩 따라 트리를 내려가며 부분일치로 후보를 좁힌다.
        반환값은 (전체 경로 이름, cortarNo) 목록.
        """
        frontier: list[tuple[str, str]] = [("", ROOT_CORTAR_NO)]

        for token in query.split():
            matches: list[tuple[str, str]] = []
            for path, code in frontier:
                for region in await self._subregions(code):
                    name = str(region.get("cortarName") or "")
                    if token in name:
                        full = f"{path} {name}".strip()
                        matches.append((full, str(region["cortarNo"])))
            if not matches:
                return []
            frontier = matches

        return frontier

    async def probe(self) -> dict[str, object]:
        """인증 핸드셰이크와 샘플 API 호출을 점검한다(noti doctor).

        네이버가 내부 API 를 바꾸면 여기 결과만 보고도 어디서 깨졌는지 알 수 있다.
        """
        result: dict[str, object] = {}
        try:
            token = await self._ensure_token(force=True)
            result["token"] = f"{token[:12]}…({len(token)}자)"
        except NaverLandError as exc:
            result["token_error"] = str(exc)
        result["handshake"] = self.last_handshake
        if "token" not in result:
            return result

        try:
            regions = await self.fetch_regions(ROOT_CORTAR_NO)
            result["regions_sample"] = [r.get("cortarName") for r in regions[:3]]
        except (NaverLandError, httpx.HTTPError) as exc:
            result["regions_error"] = f"{type(exc).__name__}: {exc}"
        return result

    async def fetch_regions(self, cortar_no: str) -> list[dict]:
        """하위 지역(법정동) 목록. cortar_no 를 찾을 때 쓰는 헬퍼."""
        payload = await self._get_json("/api/regions/list", {"cortarNo": cortar_no})
        return payload.get("regionList") or []
