"""step-up 계층 — ★기본이 OFF 인지, 켜면 어떻게 되는지.

★이 변경의 안전 근거는 "기본 OFF 라 동작이 안 바뀐다" 이다. 그것부터 시험한다.
  (ms님 fccc5f2 를 main 위에 다시 얹으면서 함께 넣음)
"""

from __future__ import annotations

import importlib

import pytest


def _reload(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from app import config as c
    importlib.reload(c)
    from app import main as m
    importlib.reload(m)
    return m


def test_기본은_꺼져_있다(monkeypatch):
    """★설정을 아무것도 안 주면 None — 지금까지와 동작이 같다."""
    monkeypatch.delenv("STEP_UP_ENABLED", raising=False)
    m = _reload(monkeypatch)
    assert m.settings.step_up_enabled is False
    for n in (0, 1, 2, 5, 99):
        assert m._step_up_tier(n) is None


def test_켜면_세션_재도전에_따라_계층이_오른다(monkeypatch):
    m = _reload(monkeypatch, STEP_UP_ENABLED="1")
    # 기본 계층 "2,3,17,1;4,5,19,2;6,8,21,2" · tier2_at=1 · tier3_at=2
    assert m._step_up_tier(0) == (2, 3, 17, 1)
    assert m._step_up_tier(1) == (4, 5, 19, 2)
    assert m._step_up_tier(2) == (6, 8, 21, 2)
    assert m._step_up_tier(9) == (6, 8, 21, 2)   # 마지막 계층에서 멈춘다


def test_계층_설정이_망가져도_안_터진다(monkeypatch):
    """★잘못된 값이 들어와도 예외 대신 None — 캡차가 통째로 죽으면 안 된다."""
    m = _reload(monkeypatch, STEP_UP_ENABLED="1", STEP_UP_TIERS="망가진값")
    assert m._step_up_tier(0) is None
    m = _reload(monkeypatch, STEP_UP_ENABLED="1", STEP_UP_TIERS="")
    assert m._step_up_tier(0) is None


def test_계층_수가_적어도_마지막_것을_쓴다(monkeypatch):
    m = _reload(monkeypatch, STEP_UP_ENABLED="1", STEP_UP_TIERS="2,3,17,1")
    assert m._step_up_tier(0) == (2, 3, 17, 1)
    assert m._step_up_tier(5) == (2, 3, 17, 1)
