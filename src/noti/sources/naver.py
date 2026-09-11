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
BOOTSTRAP_URL = f"{BASE_URL}/complexes"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)


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
            headers={"User-Agent": USER_AGENT, "Referer": f"{BASE_URL}/"},
            follow_redirects=True,
        )
        self._request_delay = request_delay
        self._token: str | None = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _ensure_token(self, *, force: bool = False) -> str:
        if self._token and not force:
            return self._token

        response = await self._client.get(BOOTSTRAP_URL)
        response.raise_for_status()
        token = self._client.cookies.get("REALESTATE")
        if not token:
            raise NaverLandError(
                "REALESTATE 토큰 쿠키를 받지 못했습니다. 네이버가 인증 방식을 바꿨을 수 있습니다."
            )
        self._token = token
        return token

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
        """대상(단지/지역)의 매물을 페이지 단위로 모아서 반환한다."""
        listings: list[Listing] = []
        seen: set[str] = set()

        for page in range(1, target.max_pages + 1):
            if page > 1:
                await asyncio.sleep(self._request_delay)

            payload = await self._fetch_page(target, page)
            articles = payload.get("articleList") or []
            for raw in articles:
                listing = Listing.from_api(raw)
                if listing.article_no in seen:
                    continue
                seen.add(listing.article_no)
                listings.append(listing)

            if not payload.get("isMoreData"):
                break

        logger.debug("target=%s 매물 %d건 조회", target.name, len(listings))
        return listings

    async def _fetch_page(self, target: Target, page: int) -> dict:
        trade_type = ":".join(target.trade_types)
        real_estate_type = ":".join(target.real_estate_types)
        params: dict[str, str | int] = {
            "realEstateType": real_estate_type,
            "tradeType": trade_type,
            "page": page,
            "order": "rank",
            "priceType": "RETAIL",
            "articleState": "",
        }

        if target.kind == "complex":
            assert target.complex_no
            params["complexNo"] = target.complex_no
            return await self._get_json(f"/api/articles/complex/{target.complex_no}", params)

        assert target.cortar_no
        params["cortarNo"] = target.cortar_no
        return await self._get_json("/api/articles", params)

    async def fetch_regions(self, cortar_no: str) -> list[dict]:
        """하위 지역(법정동) 목록. cortar_no 를 찾을 때 쓰는 헬퍼."""
        payload = await self._get_json("/api/regions/list", {"cortarNo": cortar_no})
        return payload.get("regionList") or []
