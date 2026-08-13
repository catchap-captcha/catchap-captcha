"""헤드리스 흔적은 **서버가 헤더에서 직접** 읽어야 한다 (2026-08-13).

`client_signals` 는 화면 자바스크립트가 신고하는 값이라 봇이 거짓말하면 그만이다.
흔히 쓰는 회피 두 줄이면 점수가 160에서 0으로 떨어진다(실측).

    평범한 Playwright   webdriver=true · headlessUA=true   -> 160  차단
    회피 두 줄 추가     webdriver=false · headlessUA=false ->   0  통과

그런데 같은 요청의 `Sec-CH-UA` 헤더에는 `"HeadlessChrome"` 이 그대로 남아 있었다.
브라우저가 붙이고 서버가 받는 값이라 그 회피로는 안 바뀐다.

★오탐 쪽이 더 중요하다. Safari·Firefox 는 이 헤더를 아예 안 보낸다. 없는 것을 의심으로
치면 그 브라우저 사용자 전원이 걸린다 — 아이폰·맥 사용자가 통째로 막히는 실수다.
"""

from __future__ import annotations

from app.config import settings
from app.main import automation_score, headless_brand

# 2026-08-13 에 실제로 받은 값들.
HEADLESS = '"HeadlessChrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"'
CHROME = '"Chromium";v="140", "Not=A?Brand";v="24", "Google Chrome";v="140"'
EDGE = '"Microsoft Edge";v="140", "Chromium";v="140", "Not=A?Brand";v="24"'
# 회피를 끝낸 봇이 신고하는 값 — 전부 깨끗하다.
CLEAN_SIGNALS = {"webdriver": False, "headlessUA": False, "languages": 1, "cores": 8}


def test_거짓말한_봇도_헤더로_잡힌다():
    """★신고값이 전부 깨끗해도 헤더 하나로 차단에 닿아야 한다."""
    assert automation_score(CLEAN_SIGNALS, HEADLESS) >= settings.behavior_block_score


def test_평범한_크롬은_0점():
    assert automation_score(CLEAN_SIGNALS, CHROME) == 0


def test_엣지도_0점():
    """`Chromium` 이 들어 있다고 걸리면 안 된다 — 크롬 계열 전부가 걸린다."""
    assert automation_score(CLEAN_SIGNALS, EDGE) == 0


def test_헤더가_없으면_0점():
    """★Safari·Firefox 는 이 헤더를 안 보낸다. 없는 것을 의심으로 치면 안 된다."""
    assert automation_score(CLEAN_SIGNALS, None) == 0
    assert automation_score(CLEAN_SIGNALS, "") == 0
    assert not headless_brand(None)


def test_신고값이_없어도_헤더는_본다():
    """`client_signals` 를 통째로 빼는 봇도 있다. 그때도 헤더는 남는다."""
    assert automation_score(None, HEADLESS) >= settings.behavior_block_score
    assert automation_score(None, CHROME) == 0


def test_기존_신고값_점수는_그대로():
    """동작을 바꾸지 않은 부분 — 평범한 Playwright 는 예전처럼 잡힌다."""
    naive = {"webdriver": True, "headlessUA": True, "languages": 1, "cores": 8}
    assert automation_score(naive, None) == 160
