"""CLI 엔트리포인트: noti run / once / test-notify / regions."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from .config import Settings, WatchConfig
from .notifiers import ConsoleNotifier, Notifier, TelegramNotifier
from .service import MonitorService
from .sources import create_client


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
    config_path = args.config or settings.config_path
    try:
        config = WatchConfig.load(config_path)
    except FileNotFoundError:
        # 트레이스백 대신 무엇을 해야 하는지 알려준다(도커에서 특히 헷갈린다).
        print(
            f"설정 파일이 없습니다: {config_path}\n"
            "  - 직접 실행: cp config.example.yaml config.yaml\n"
            "  - 도커: 호스트의 ./config 디렉터리에 config.yaml 을 두세요\n"
            "    (.env 에 NOTI_CONFIG_PATH 가 남아 있으면 컨테이너 경로를 덮어씁니다)",
            file=sys.stderr,
        )
        return 1
    notifier = build_notifier(settings, force_console=args.console)

    client = create_client(settings)
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


TOKEN_HOWTO = """토큰 얻는 법 (2분):
  1. 크롬에서 https://new.land.naver.com 접속
  2. F12 → Network 탭 → 필터에 articles 입력
  3. 지도에서 아무 지역이나 클릭 (api/articles... 요청이 뜬다)
  4. 그 요청 클릭 → Request Headers 의 authorization 값 복사
  5. .env 에 NOTI_NAVER_AUTH_TOKEN=eyJ... (앞의 Bearer 는 있어도 되고 없어도 됨)
  6. docker compose up -d  (또는 systemctl restart noti)"""


async def _doctor(args: argparse.Namespace) -> int:
    """네이버 접속·인증이 어디서 막히는지 점검한다."""
    settings = Settings()
    client = create_client(settings)
    try:
        if getattr(args, "raw", False):
            if not hasattr(client, "dump_raw"):
                print("--raw 는 mobile 소스에서만 지원합니다.", file=sys.stderr)
                return 1
            for sample in await client.dump_raw():
                print(f"===== {sample['label']}  [{sample.get('status', '-')}] {sample.get('path')}")
                print(f"  params: {sample.get('params')}")
                if sample.get("error"):
                    print(f"  오류: {sample['error']}")
                print(sample.get("body") or "(빈 응답)")
                print()
            return 0
        result = await client.probe()
    finally:
        await client.aclose()

    source = result.get("source", "desktop")
    print(f"== 소스: {source} ==")
    if source == "desktop":
        print(
            "  직접 설정한 토큰(NOTI_NAVER_AUTH_TOKEN): "
            + ("있음" if result.get("manual_token") else "없음")
        )
        print("\n== 쿠키 핸드셰이크 ==")
    for attempt in result.get("handshake", []) if source == "desktop" else []:
        if "error" in attempt:
            print(f"  {attempt['path']:<12} 오류: {attempt['error']}")
            continue
        mark = "토큰 받음" if attempt["got_token"] else "토큰 없음"
        print(f"  {attempt['path']:<12} {attempt['status']} → {attempt['final_url']}  [{mark}]")
        print(f"  {'':<12} 쿠키: {', '.join(attempt['cookies']) or '(없음)'}")

    if "token_error" in result:
        print(f"\n결과: 토큰을 받지 못했습니다.\n  {result['token_error']}")
        return 1

    if "token" in result:
        print(f"\n토큰: {result['token']}")
        print(f"Authorization: {result.get('auth_header')}")

    if "regions_error" in result:
        print(f"지역 목록 조회 실패: {result['regions_error']}")
        print("\n네이버에 접속 자체가 안 되는 상태입니다(차단·네트워크 확인).")
        return 1
    print(f"지역 목록 조회 성공: {result.get('regions_sample')}")

    if "articles_error" in result:
        # 매물 목록만 실패하면 인증 토큰 문제다(지역 목록은 인증이 필요 없다).
        print(f"매물 목록 조회 실패: {result['articles_error']}")
        if source == "desktop":
            print(
                "\n데스크톱(new.land) 매물 API 는 브라우저의 JS 가 만드는 토큰을 요구합니다.\n"
                "NOTI_SOURCE=mobile 로 두면 토큰 없이 동작합니다(기본값).\n" + TOKEN_HOWTO
            )
        return 1

    if "clusters" in result:
        print(f"지도 클러스터: {result['clusters']}개")
    count = result["articles_count"]
    print(f"매물 목록 조회: 역삼동 {count}건")
    for sample in result.get("articles_sample", []):
        print(f"  · {sample}")

    if count == 0:
        # 0건은 '정상'이 아니다. 어느 단계가 비었는지 알려준다.
        stage = "클러스터" if result.get("clusters") == 0 else "매물 목록"
        print(
            f"\n{stage} 단계에서 빈 응답이 왔습니다. 파라미터가 맞지 않을 가능성이 큽니다.\n"
            "`noti doctor --raw` 로 어떤 조합이 데이터를 주는지 비교해 보세요."
        )
        return 1

    print("\n정상입니다.")
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
    client = create_client(settings)
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
    client = create_client(settings)
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

    p = sub.add_parser("doctor", help="네이버 접속·인증 점검 (문제 생겼을 때 먼저 실행)")
    p.add_argument("--raw", action="store_true", help="응답 원문을 그대로 출력(필드명 확인용)")

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
        "doctor": _doctor,
    }
    return asyncio.run(handlers[args.command](args))


if __name__ == "__main__":
    raise SystemExit(main())
