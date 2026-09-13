"""설정 페이지(로컬 관리 UI) + JSON API.

인증이 없으므로 기본은 127.0.0.1 바인딩이다. 외부에 열지 말 것.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from .config import Settings, Target, WatchConfig
from .filters import matches
from .notifiers import TelegramNotifier
from .sources import NaverLandClient, NaverLandError

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"


class PreviewRequest(BaseModel):
    target: Target
    limit: int = 20


class PreviewResponse(BaseModel):
    scopes: int  # 실제 조회한 지역/단지 수
    fetched: int  # 조회된 전체 매물 수
    matched: int  # 조건을 통과한 수
    listings: list[dict[str, Any]]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.client = NaverLandClient(
            auth_token=settings.naver_auth_token,
            timeout=settings.request_timeout_seconds,
            request_delay=settings.request_delay_seconds,
        )
        try:
            yield
        finally:
            await app.state.client.aclose()

    app = FastAPI(title="noti 설정", lifespan=lifespan, docs_url="/api/docs")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/settings")
    async def read_settings() -> dict[str, Any]:
        """페이지 상단에 보여줄 런타임 정보(비밀값은 노출하지 않는다)."""
        return {
            "config_path": str(settings.config_path),
            "db_path": str(settings.db_path),
            "poll_interval_seconds": settings.poll_interval_seconds,
            "telegram_enabled": settings.telegram_enabled,
        }

    @app.get("/api/config")
    async def read_config() -> dict[str, Any]:
        if not settings.config_path.exists():
            return WatchConfig(targets=[]).model_dump(mode="json")
        try:
            return WatchConfig.load(settings.config_path).model_dump(mode="json")
        except (ValidationError, ValueError) as exc:
            raise HTTPException(400, f"설정 파일을 읽지 못했습니다: {exc}") from exc

    @app.put("/api/config")
    async def write_config(config: WatchConfig) -> dict[str, Any]:
        """검증에 통과한 설정만 저장한다. 저장 즉시 감시 루프가 다음 사이클에 반영한다."""
        config.save(settings.config_path)
        logger.info("설정 저장: 대상 %d개", len(config.targets))
        return {"saved": True, "targets": len(config.targets)}

    @app.get("/api/regions/search")
    async def search_regions(q: str) -> list[dict[str, str]]:
        if not q.strip():
            return []
        matches_ = await _call_naver(app.state.client.search_regions(q))
        return [{"cortar_no": code, "path": path} for path, code in matches_]

    @app.get("/api/regions/children")
    async def region_children(cortar_no: str) -> list[dict[str, str]]:
        regions = await _call_naver(app.state.client.fetch_regions(cortar_no))
        return [
            {"cortar_no": str(r["cortarNo"]), "name": str(r.get("cortarName") or "")}
            for r in regions
            if r.get("cortarNo")
        ]

    @app.post("/api/preview")
    async def preview(request: PreviewRequest) -> PreviewResponse:
        """조건을 저장하기 전에 어떤 매물이 걸리는지 미리 본다(알림은 보내지 않음)."""
        client: NaverLandClient = app.state.client
        scopes = await _call_naver(client.resolve_scopes(request.target))
        listings = await _call_naver(client.fetch_listings(request.target))
        matched = [x for x in listings if matches(x, request.target.criteria)]
        return PreviewResponse(
            scopes=len(scopes),
            fetched=len(listings),
            matched=len(matched),
            listings=[_listing_json(x) for x in matched[: request.limit]],
        )

    @app.post("/api/test-notify")
    async def test_notify() -> dict[str, bool]:
        if not settings.telegram_enabled:
            raise HTTPException(400, "텔레그램 토큰/챗ID 가 설정되어 있지 않습니다(.env 확인)")
        assert settings.telegram_bot_token and settings.telegram_chat_id
        notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
        try:
            await notifier.send_text("✅ noti 설정 페이지에서 보낸 테스트 메시지입니다.")
        finally:
            await notifier.aclose()
        return {"sent": True}

    return app


def _listing_json(listing) -> dict[str, Any]:
    data = asdict(listing)
    data["url"] = listing.url
    data["summary"] = listing.summary()
    return data


async def _call_naver(awaitable):
    """네이버 쪽 실패를 502 로 바꿔 페이지에서 메시지로 보여준다."""
    try:
        return await awaitable
    except NaverLandError as exc:
        raise HTTPException(502, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(502, f"네이버 부동산에 연결하지 못했습니다: {exc}") from exc
