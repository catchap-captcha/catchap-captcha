"""PoW 난이도는 **사람이 견딜 수 있는 선** 안에 있어야 한다 (2026-08-14).

위젯은 손으로 짠 자바스크립트 sha256 을 쓴다(`catchapGuardPow.js`). 네이티브 암호
라이브러리가 아니다. 데스크톱 크로미움 실측 **초당 477,453회** 기준:

    비트    기대 시간      예전 주석
    17        275ms        0.03초    ← 9배 차이
    19        1.1초           —
    21        4.4초         0.4초    ← 11배 차이
    24       35.1초         3.2초    ← 11배 차이

예전 주석은 네이티브로 잰 값으로 보인다. 그대로 두면 읽는 사람이 안전한 값이라고
착각한다 — 24비트는 **35초** 이고, 화면이 멈춘 것처럼 보인다.

★비용이 비대칭이다. 봇은 대개 네이티브 암호를 쓰므로 우리 위젯보다 훨씬 빠르다
(0814 실측: Node 네이티브로 17비트를 즉시). 난이도를 올릴수록 **사람이 더 많이 아프다.**
"""

from __future__ import annotations

from app.config import settings

# 위젯의 자바스크립트 sha256 실측 (데스크톱 크로미움, 2026-08-14)
HASHES_PER_SECOND = 477_453
# 오탐을 맞은 사람이 기다리게 되는 시간의 상한. 그 이상은 벌이 아니라 고장으로 보인다.
TOLERABLE_SECONDS = 6.0


def _seconds(bits: int) -> float:
    return (2 ** bits) / HASHES_PER_SECOND


def test_상한이_사람이_견딜_수_있는_선_안에_있다():
    """★24비트면 35초다. 그 대상은 의심이 붙은 채 3회 틀린 사람인데, 우리 판정에는
    오탐이 있다(특정 참가자 14.1%). 오탐을 맞은 사람이 35초를 본다."""
    assert _seconds(settings.pow_max_bits) <= TOLERABLE_SECONDS


def test_한_번_틀렸을_때가_과하지_않다():
    """`POW_STEPUP_FAILURES=1` 이라 **한 번만 틀려도** 올라간다. 사람 오답률이 12.8%
    이라 여덟 명 중 한 명이 여기 걸린다."""
    once = settings.pow_difficulty_bits + settings.pow_stepup_bits
    assert _seconds(min(once, settings.pow_max_bits)) <= 2.0


def test_평소_난이도는_거의_체감되지_않는다():
    assert _seconds(settings.pow_difficulty_bits) <= 0.5


def test_상한이_평소보다는_높다():
    """비용 층 자체는 유지해야 한다 — 봇에게 판당 값을 물리는 것이 목적이다."""
    assert settings.pow_max_bits > settings.pow_difficulty_bits
