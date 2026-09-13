"""토큰 핸드셰이크 동작 (네이버 라우팅이 바뀌어도 버티는지)."""

import httpx
import pytest

from noti.sources import NaverLandClient, NaverLandError
from noti.sources.naver import TOKEN_COOKIE, _normalize_token


def client_with(handler) -> NaverLandClient:
    client = NaverLandClient(request_delay=0)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True, base_url=""
    )
    return client


@pytest.mark.asyncio
async def test_falls_back_to_next_path_when_first_redirects():
    """실제 증상: /complexes 가 /404 로 302 되면서 쿠키를 못 받는다."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/complexes":
            return httpx.Response(302, headers={"Location": "https://new.land.naver.com/404"})
        if path == "/404":
            return httpx.Response(200, text="not found")
        if path == "/":
            return httpx.Response(200, headers={"Set-Cookie": f"{TOKEN_COOKIE}=jwt-token"})
        return httpx.Response(200)

    client = client_with(handler)
    try:
        assert await client._ensure_token() == "jwt-token"
        assert client.last_handshake[0]["got_token"] is True  # "/" 에서 바로 받는다
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_error_lists_every_attempt_when_no_cookie():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://new.land.naver.com/404"}) \
            if request.url.path != "/404" else httpx.Response(200)

    client = client_with(handler)
    try:
        with pytest.raises(NaverLandError) as exc:
            await client._ensure_token()
        message = str(exc.value)
        assert "/complexes" in message and "/404" in message  # 어디서 막혔는지 보인다
        assert len(client.last_handshake) == 5
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_probe_reports_token_and_regions():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/regions/list":
            return httpx.Response(200, json={"regionList": [{"cortarName": "서울시"}]})
        return httpx.Response(200, headers={"Set-Cookie": f"{TOKEN_COOKIE}=abcdefghijklmnop"})

    client = client_with(handler)
    try:
        result = await client.probe()
        assert result["token"].startswith("abcdefghijkl")
        assert result["regions_sample"] == ["서울시"]
    finally:
        await client.aclose()


def token_server(accepted: str):
    """accepted 와 정확히 일치하는 Authorization 만 통과시키는 가짜 서버."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/"):
            if request.headers.get("Authorization") != accepted:
                return httpx.Response(401, json={"error": "unauthorized"})
            return httpx.Response(200, json={"articleList": [{"articleNo": "1"}]})
        # URL 인코딩된 'Bearer%20<jwt>' 형태로 쿠키를 내려준다
        return httpx.Response(200, headers={"Set-Cookie": f"{TOKEN_COOKIE}=Bearer%20jwt-value"})

    return handler


@pytest.mark.asyncio
async def test_bearer_prefix_in_cookie_is_not_doubled():
    """쿠키가 'Bearer%20eyJ...' 여도 Authorization 은 'Bearer eyJ...' 여야 한다."""
    client = client_with(token_server("Bearer jwt-value"))
    try:
        payload = await client._get_json("/api/articles", {"cortarNo": "1168010100"})
        assert payload["articleList"]
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_falls_back_to_raw_cookie_form():
    """서버가 쿠키 원문 표기를 요구하면 그쪽으로 넘어간다."""
    client = client_with(token_server("Bearer%20jwt-value"))
    try:
        payload = await client._get_json("/api/articles", {"cortarNo": "1168010100"})
        assert payload["articleList"]
        assert client._auth_index != 0  # 통한 표기를 기억한다
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_gives_up_after_refreshing_once():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/"):
            calls.append(request.headers.get("Authorization"))
            return httpx.Response(401, json={"error": "nope"})
        return httpx.Response(200, headers={"Set-Cookie": f"{TOKEN_COOKIE}=jwt"})

    client = client_with(handler)
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await client._get_json("/api/articles", {})
        assert len(calls) == 4  # 후보 2개 × (최초 + 재발급 후) 1회씩
    finally:
        await client.aclose()


def api_server(accepted_bearer: str):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/"):
            if request.headers.get("Authorization") != accepted_bearer:
                return httpx.Response(401, json={"error": "unauthorized"})
            return httpx.Response(200, json={"articleList": [{"articleNo": "1"}]})
        return httpx.Response(200, headers={"Set-Cookie": f"{TOKEN_COOKIE}=Mon%20Sep%2014%202026"})

    return handler


def client_with_token(handler, token: str) -> NaverLandClient:
    client = client_with(handler)
    client.auth_token = _normalize_token(token)
    return client


@pytest.mark.parametrize(
    "supplied",
    ["eyJhbGciOi.payload.sig", "Bearer eyJhbGciOi.payload.sig", '"eyJhbGciOi.payload.sig"'],
)
@pytest.mark.asyncio
async def test_manual_token_is_normalized(supplied):
    """Bearer 접두사나 따옴표를 붙여 복사해도 동작한다."""
    client = client_with_token(api_server("Bearer eyJhbGciOi.payload.sig"), supplied)
    try:
        payload = await client._get_json("/api/articles", {"cortarNo": "1168010100"})
        assert payload["articleList"]
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_expired_manual_token_raises_auth_error():
    """만료된 토큰이면 재시도 대신 갱신 안내를 담은 예외를 낸다."""
    from noti.sources import NaverAuthError

    client = client_with_token(api_server("Bearer valid"), "expired")
    try:
        with pytest.raises(NaverAuthError) as exc:
            await client._get_json("/api/articles", {})
        assert "NOTI_NAVER_AUTH_TOKEN" in str(exc.value)
    finally:
        await client.aclose()
