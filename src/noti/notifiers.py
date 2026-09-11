"""알림 채널. 기본은 텔레그램, 개발 중에는 콘솔."""

from __future__ import annotations

import html
import logging
from typing import Protocol

import httpx

from .models import Listing

logger = logging.getLogger(__name__)


class Notifier(Protocol):
    async def send(self, target_name: str, listing: Listing) -> None: ...

    async def send_text(self, text: str) -> None: ...

    async def aclose(self) -> None: ...


def format_message(target_name: str, listing: Listing) -> str:
    """텔레그램 HTML 파스 모드용 메시지."""
    lines = [
        f"🏠 <b>{html.escape(target_name)}</b> 새 매물",
        f"<b>{html.escape(listing.name)}</b> {html.escape(listing.summary())}",
    ]
    if listing.feature_desc:
        lines.append(html.escape(listing.feature_desc))
    if listing.realtor:
        lines.append(f"중개사: {html.escape(listing.realtor)}")
    if listing.confirm_date:
        lines.append(f"확인일: {html.escape(listing.confirm_date)}")
    lines.append(f'<a href="{listing.url}">네이버 부동산에서 보기</a>')
    return "\n".join(lines)


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str, *, timeout: float = 10.0) -> None:
        self._chat_id = chat_id
        self._client = httpx.AsyncClient(
            base_url=f"https://api.telegram.org/bot{bot_token}", timeout=timeout
        )

    async def send(self, target_name: str, listing: Listing) -> None:
        await self.send_text(format_message(target_name, listing))

    async def send_text(self, text: str) -> None:
        response = await self._client.post(
            "/sendMessage",
            json={
                "chat_id": self._chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
        )
        if response.is_error:
            # 알림 실패로 서비스가 죽지 않도록 로그만 남긴다.
            logger.error("텔레그램 전송 실패 %s: %s", response.status_code, response.text[:200])

    async def aclose(self) -> None:
        await self._client.aclose()


class ConsoleNotifier:
    """토큰 없이 동작을 확인할 때 쓰는 표준출력 알림."""

    async def send(self, target_name: str, listing: Listing) -> None:
        print(f"[{target_name}] {listing.name} | {listing.summary()} | {listing.url}")

    async def send_text(self, text: str) -> None:
        print(text)

    async def aclose(self) -> None:
        return None
