"""429 의 두 헤더는 **다른 출처의 브라우저에서도 읽혀야** 한다.

화면은 www 에서 돌고 캡차는 captcha 도메인이라 출처가 다르다. 다른 출처의 응답에서
JS 가 읽을 수 있는 헤더는 정해져 있어서, `Access-Control-Expose-Headers` 에 적지 않으면
`res.headers.get('Retry-After')` 이 조용히 null 을 돌려준다.

그러면 프론트가 기본값으로 떨어져 **차단이 전부 "5초 뒤에 다시 시도하세요" 로 그려진다.**
사용자는 300초 막힌 걸 잠깐 기다리면 되는 줄 안다.

실측(운영, 2026-08-13): 응답은 `Retry-After=272 · X-Captcha-Retry-Reason=blocked`,
화면은 "2초 뒤에 다시 시도할 수 있습니다".

앱을 띄우지 않고 미들웨어 설정만 본다 — 기동에는 DB 가 필요한데, 이 시험이 보려는
것은 DB 와 아무 상관이 없다.
"""

from __future__ import annotations

from fastapi.middleware.cors import CORSMiddleware

from app.main import app


def _exposed() -> set[str]:
    for middleware in app.user_middleware:
        if middleware.cls is CORSMiddleware:
            names = middleware.kwargs.get("expose_headers", [])
            return {str(name).lower() for name in names}
    raise AssertionError("CORS 미들웨어가 없습니다")


def test_남은_시간_헤더가_열려_있다():
    """★닫히면 차단이 '잠깐 대기' 로 그려진다."""
    assert "retry-after" in _exposed()


def test_차단_사유_헤더가_열려_있다():
    """대기와 차단을 가르는 유일한 신호다 — 못 읽으면 둘이 같은 화면이 된다."""
    assert "x-captcha-retry-reason" in _exposed()
