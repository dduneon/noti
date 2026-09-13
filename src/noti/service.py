"""폴링 루프: 조회 → 조건 필터 → 중복 제거 → 알림."""

from __future__ import annotations

import asyncio
import logging
import random

import httpx

from .config import Settings, Target, WatchConfig
from .filters import matches
from .models import Listing
from .notifiers import Notifier
from .sources import NaverAuthError, NaverLandClient, NaverLandError
from .store import GLOBAL_SCOPE, Store

logger = logging.getLogger(__name__)


class MonitorService:
    def __init__(
        self,
        settings: Settings,
        config: WatchConfig,
        client: NaverLandClient,
        store: Store,
        notifier: Notifier,
    ) -> None:
        self._settings = settings
        self._config = config
        self._client = client
        self._store = store
        self._notifier = notifier
        self._config_mtime: float | None = None
        self._auth_alert_sent = False

    async def _alert_auth_failure(self, exc: Exception) -> None:
        """토큰 갱신이 필요하다는 걸 텔레그램으로 한 번 알린다(프로세스당 1회)."""
        if self._auth_alert_sent:
            return
        self._auth_alert_sent = True
        try:
            await self._notifier.send_text(
                "⚠️ 네이버 매물 조회가 인증 오류로 실패하고 있습니다.\n"
                f"{exc}\n"
                "새 토큰으로 NOTI_NAVER_AUTH_TOKEN 을 갱신해 주세요."
            )
        except Exception:
            logger.exception("인증 실패 알림 전송에 실패했습니다")

    def _reload_config_if_changed(self) -> None:
        """설정 페이지에서 저장한 내용을 재시작 없이 반영한다."""
        path = self._settings.config_path
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return
        if self._config_mtime is None:
            self._config_mtime = mtime
            return
        if mtime == self._config_mtime:
            return

        try:
            self._config = WatchConfig.load(path)
        except Exception:
            logger.exception("바뀐 설정을 읽지 못해 이전 설정을 유지합니다: %s", path)
        else:
            logger.info("설정을 다시 읽었습니다: 대상 %d개", len(self._config.targets))
        self._config_mtime = mtime

    async def run_forever(self) -> None:
        logger.info(
            "감시 시작: 대상 %d개, 주기 %d초",
            len(self._config.targets),
            self._settings.poll_interval_seconds,
        )
        while True:
            try:
                await self.run_once()
            except Exception:  # 한 사이클이 실패해도 루프는 계속
                logger.exception("사이클 실행 중 오류")

            delay = self._settings.poll_interval_seconds + random.uniform(
                0, self._settings.jitter_seconds
            )
            await asyncio.sleep(delay)

    async def run_once(self) -> list[Listing]:
        """전체 대상을 한 번 돌고, 이번에 알린 매물을 반환한다."""
        self._reload_config_if_changed()
        notified: list[Listing] = []

        for index, target in enumerate(self._config.targets):
            if index > 0:
                await asyncio.sleep(self._settings.request_delay_seconds)
            try:
                notified.extend(await self._process_target(target))
            except NaverAuthError as exc:
                # 토큰 문제는 대상마다 반복해도 소용없다. 한 번 알리고 이번 사이클은 중단.
                logger.error("네이버 인증 실패: %s", exc)
                await self._alert_auth_failure(exc)
                break
            except (NaverLandError, httpx.HTTPError, OSError) as exc:
                logger.warning("target=%s 조회 실패: %s", target.name, exc)
            except Exception:
                logger.exception("target=%s 처리 실패", target.name)

        return notified

    @property
    def _dedupe_scope(self) -> str:
        """global 이면 대상이 겹쳐도 같은 집은 한 번만 알린다."""
        return GLOBAL_SCOPE if self._config.dedupe_scope == "global" else ""

    async def _process_target(self, target: Target) -> list[Listing]:
        scope = self._dedupe_scope or target.name
        listings = await self._client.fetch_listings(target)
        self._auth_alert_sent = False  # 한 번이라도 성공하면 다음 실패 때 다시 알린다
        matched = [listing for listing in listings if matches(listing, target.criteria)]
        fresh = self._store.filter_new(
            scope,
            matched,
            merge_same_property=self._config.merge_same_property,
            notify_on_price_change=self._config.notify_on_price_change,
        )

        # 첫 실행에는 기존 매물이 전부 '새 매물'이라 알림 폭탄이 된다.
        first_run = not self._store.is_bootstrapped(target.name)
        should_notify = not first_run or self._config.notify_on_first_run

        logger.info(
            "target=%s 조회 %d건 / 조건일치 %d건 / 신규 %d건%s",
            target.name,
            len(listings),
            len(matched),
            len(fresh),
            "" if should_notify else " (첫 실행이라 알림 생략)",
        )

        sent: list[Listing] = []
        if should_notify:
            limit = self._settings.max_notifications_per_cycle
            for listing in fresh[:limit]:
                await self._notifier.send(target.name, listing)
                sent.append(listing)
            if len(fresh) > limit:
                await self._notifier.send_text(
                    f"[{target.name}] 조건에 맞는 신규 매물이 {len(fresh)}건이라 "
                    f"{limit}건만 보냈습니다."
                )

        # 알림을 생략했어도 본 매물은 기록해 둔다(다음 사이클부터 진짜 신규만 알림).
        self._store.remember(
            scope, matched, merge_same_property=self._config.merge_same_property
        )
        self._store.mark_bootstrapped(target.name)
        return sent
