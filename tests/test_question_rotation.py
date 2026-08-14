"""출제 순서가 **훑는 쪽에 안내 투어가 되지 않아야** 한다 (2026-08-14).

노출 적은 문항부터 정확히 내보내면 공평하지만, 문항을 받아가려는 쪽에는 **중복 없는
목록**을 순서대로 내주는 셈이 된다.

실측(운영, 2026-08-14):

    활성 문항        2,003개
    노출 횟수        전부 3~4회      ← 균등하다는 증거이자, 훑기가 끝났다는 증거
    IP당 발급 상한   분당 30개
    → 2,003 / 30 = 약 67분이면 IP 하나로 은행을 한 바퀴 다 본다

섞으면 같은 문항이 다시 나오므로(쿠폰 수집가 문제) 한 바퀴에 약 16,400번이 들고,
같은 상한에서 67분이 **9시간**이 된다.

문항을 다 받아가면 지시문과 조각 그림이 같이 딸려 오므로 정답까지 라벨링할 수 있다.
그러면 "무엇을 풀었는가" 는 방어선이 아니게 된다 — 행동 판별이 남는 이유다.
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.db import Database


class _FakeCursor:
    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple]] = []
        self._answer = None

    def execute(self, sql: str, args: tuple = ()) -> None:
        self.statements.append((sql, args))
        low = sql.lower()
        if "from captcha_questions" in low:
            self._answer = {"id": "q-1"}
        elif "from captcha_objects" in low:
            self._answer = []
        else:
            self._answer = {}

    def fetchone(self):
        return self._answer if isinstance(self._answer, dict) else None

    def fetchall(self):
        return self._answer if isinstance(self._answer, list) else []

    def __enter__(self): return self
    def __exit__(self, *exc): return False


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None: self._cursor = cursor
    def cursor(self): return self._cursor
    def commit(self) -> None: pass
    def __enter__(self): return self
    def __exit__(self, *exc): return False


def _pick() -> _FakeCursor:
    cursor = _FakeCursor()
    database = Database.__new__(Database)
    database.settings = settings
    database.connection = lambda *a, **k: _FakeConnection(cursor)  # type: ignore[method-assign]
    database.active_question()
    return cursor


def _selects(cursor: _FakeCursor) -> list[str]:
    return [sql for sql, _ in cursor.statements if "from captcha_questions" in sql.lower()]


def test_출제_순서가_노출순으로_고정되지_않는다():
    """★고정이면 훑는 쪽이 중복 없이 은행을 한 바퀴 받아간다."""
    for sql in _selects(_pick()):
        assert "served_count asc" not in " ".join(sql.lower().split())


def test_출제_순서에_무작위가_섞인다():
    selects = _selects(_pick())
    assert selects, "문항 고르는 질의가 안 나갔습니다"
    for sql in selects:
        assert "rand()" in sql.lower()


def test_노출_적은_쪽을_여전히_당겨온다():
    """무작위만 쓰면 어떤 문항은 오래 안 나간다. 노출 횟수가 순서에 남아 있어야 한다."""
    for sql in _selects(_pick()):
        assert "served_count" in sql.lower()


def test_섞는_폭이_현재_노출_편차보다_크다():
    """폭이 편차(1회)보다 작으면 사실상 고정된 순서와 같아진다."""
    assert settings.rotation_jitter >= 5
