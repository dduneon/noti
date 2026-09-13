"""매물 조회 소스. 기본은 토큰이 필요 없는 모바일 엔드포인트."""

from ..config import Settings
from .naver import NaverAuthError, NaverLandClient, NaverLandError
from .naver_mobile import NaverMobileClient

__all__ = [
    "NaverAuthError",
    "NaverLandClient",
    "NaverLandError",
    "NaverMobileClient",
    "create_client",
]


def create_client(settings: Settings) -> NaverLandClient | NaverMobileClient:
    """설정에 맞는 클라이언트를 만든다."""
    if settings.source == "desktop":
        return NaverLandClient(
            auth_token=settings.naver_auth_token,
            timeout=settings.request_timeout_seconds,
            request_delay=settings.request_delay_seconds,
        )
    return NaverMobileClient(
        timeout=settings.request_timeout_seconds,
        request_delay=settings.request_delay_seconds,
    )
