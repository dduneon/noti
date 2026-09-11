"""CLI 엔트리포인트: noti run / once / test-notify / regions."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from .config import Settings, WatchConfig
from .notifiers import ConsoleNotifier, Notifier, TelegramNotifier
from .service import MonitorService
from .sources import NaverLandClient


def build_notifier(settings: Settings, *, force_console: bool = False) -> Notifier:
    if force_console or not settings.telegram_enabled:
        if not force_console:
            print("텔레그램 설정이 없어 콘솔로 출력합니다.", file=sys.stderr)
        return ConsoleNotifier()
    assert settings.telegram_bot_token and settings.telegram_chat_id
    return TelegramNotifier(
        settings.telegram_bot_token,
        settings.telegram_chat_id,
        timeout=settings.request_timeout_seconds,
    )


async def _run(args: argparse.Namespace) -> int:
    settings = Settings()
    config = WatchConfig.load(args.config or settings.config_path)
    notifier = build_notifier(settings, force_console=args.console)

    client = NaverLandClient(
        timeout=settings.request_timeout_seconds,
        request_delay=settings.request_delay_seconds,
    )
    store = None
    try:
        from .store import Store

        store = Store(settings.db_path)
        service = MonitorService(settings, config, client, store, notifier)
        if args.command == "once":
            sent = await service.run_once()
            print(f"이번 실행에서 알린 매물: {len(sent)}건")
        else:
            await service.run_forever()
    finally:
        await client.aclose()
        await notifier.aclose()
        if store is not None:
            store.close()
    return 0


async def _test_notify(args: argparse.Namespace) -> int:
    settings = Settings()
    notifier = build_notifier(settings, force_console=args.console)
    try:
        await notifier.send_text("✅ noti 알림 테스트입니다.")
    finally:
        await notifier.aclose()
    return 0


def _web(args: argparse.Namespace) -> int:
    """설정 페이지 서버. fastapi/uvicorn 은 선택 의존성이라 여기서 import 한다."""
    try:
        import uvicorn

        from .web import create_app
    except ImportError:
        print(
            "설정 페이지를 쓰려면 웹 의존성이 필요합니다: pip install -e '.[web]'",
            file=sys.stderr,
        )
        return 1

    settings = Settings()
    print(f"설정 페이지: http://{args.host}:{args.port}  (설정 파일: {settings.config_path})")
    uvicorn.run(create_app(settings), host=args.host, port=args.port, log_level="info")
    return 0


async def _find_region(args: argparse.Namespace) -> int:
    settings = Settings()
    client = NaverLandClient(timeout=settings.request_timeout_seconds)
    try:
        matches = await client.search_regions(" ".join(args.query))
    finally:
        await client.aclose()

    if not matches:
        print("일치하는 지역이 없습니다. '서울 강남구' 처럼 상위 지역부터 띄어서 넣어보세요.")
        return 1
    for path, cortar_no in matches:
        print(f"{cortar_no}\t{path}")
    return 0


async def _regions(args: argparse.Namespace) -> int:
    settings = Settings()
    client = NaverLandClient(timeout=settings.request_timeout_seconds)
    try:
        for region in await client.fetch_regions(args.cortar_no):
            print(f"{region.get('cortarNo')}\t{region.get('cortarName')}")
    finally:
        await client.aclose()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="noti", description="네이버 부동산 매물 알림 봇")
    parser.add_argument("--log-level", default="INFO")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (("run", "주기적으로 감시"), ("once", "한 번만 실행")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--config", help="감시 조건 YAML 경로")
        p.add_argument("--console", action="store_true", help="텔레그램 대신 콘솔 출력")

    p = sub.add_parser("test-notify", help="알림 채널 연결 확인")
    p.add_argument("--console", action="store_true")

    p = sub.add_parser("regions", help="하위 지역 코드 목록")
    p.add_argument("cortar_no", help="상위 지역 코드. 시/도 목록은 0000000000")

    p = sub.add_parser("find-region", help="지역명으로 cortarNo 찾기 (예: 서울 강남구 역삼동)")
    p.add_argument("query", nargs="+", help="상위 지역부터 띄어쓴 이름")

    p = sub.add_parser("web", help="브라우저에서 조건을 편집하는 설정 페이지 실행")
    p.add_argument("--host", default="127.0.0.1", help="기본 127.0.0.1 (인증이 없으니 외부 노출 금지)")
    p.add_argument("--port", type=int, default=8765)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.command == "web":  # uvicorn 이 자체 이벤트 루프를 돌린다
        return _web(args)

    handlers = {
        "run": _run,
        "once": _run,
        "test-notify": _test_notify,
        "regions": _regions,
        "find-region": _find_region,
    }
    return asyncio.run(handlers[args.command](args))


if __name__ == "__main__":
    raise SystemExit(main())
