"""출제 순서가 **훑는 쪽에 안내 투어가 되지 않아야** 한다 (2026-08-14).

노출 적은 문항부터 정확히 내보내면 공평하지만, 문항을 받아가려는 쪽에는 **중복 없는
목록**을 순서대로 내주는 셈이 된다.

실측(운영, 2026-08-14):

    활성 문항        2,003개
    노출 횟수        전부 3~4회
    IP당 발급 상한   분당 30개
    → 2,003 / 30 = 약 67분이면 IP 하나로 은행을 한 바퀴 다 본다

★한 번 틀린 방법을 여기 남겨 둔다 — `served_count + RAND()*폭` 은 **효과가 없다.**
2,003행 중 최솟값 하나를 뽑는데, 노출 적은 무리가 크면 그 안에서 아주 작은 난수가
반드시 나오기 때문이다(3회 무리 533개의 최솟값 ≈ 3.047 vs 4회 무리의 ≈ 4.017 →
적은 무리가 100% 승리). 폭을 2000으로 키워도 43% 다.

지금은 지수 경주로 뽑는다 — `-LOG(1-RAND()) * (노출+1)` 의 최솟값. 뽑힐 확률이
`1/(노출+1)` 에 비례해서 적게 나온 쪽이 유리하되 많이 나온 쪽도 나온다.
"""

from __future__ import annotations

import math
import random

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


def _selects() -> list[str]:
    cursor = _FakeCursor()
    database = Database.__new__(Database)
    database.settings = settings
    database.connection = lambda *a, **k: _FakeConnection(cursor)  # type: ignore[method-assign]
    database.active_question()
    return [sql for sql, _ in cursor.statements if "from captcha_questions" in sql.lower()]


def _flat(sql: str) -> str:
    return " ".join(sql.lower().split())


def test_출제_순서가_노출순으로_고정되지_않는다():
    """★고정이면 훑는 쪽이 중복 없이 은행을 한 바퀴 받아간다."""
    for sql in _selects():
        assert "order by q.served_count asc" not in _flat(sql)


def test_노출이_더하기가_아니라_곱으로_들어간다():
    """★`served_count + RAND()*폭` 은 효과가 없다(무리가 크면 적은 쪽이 100% 이긴다).

    가중치로 곱해야 무리를 넘나든다.
    """
    selects = _selects()
    assert selects, "문항 고르는 질의가 안 나갔습니다"
    for sql in selects:
        flat = _flat(sql)
        assert "log(1 - rand())" in flat
        assert "* (q.served_count + 1)" in flat


def test_노출_적은_쪽이_여전히_유리하다():
    """무작위로 바꾸면서 공평함을 잃으면 안 된다 — 뽑힐 확률이 1/(노출+1) 에 비례한다.

    SQL 과 같은 규칙을 파이썬으로 돌려 확인한다. 적게 나온 무리(533개, 3회)가
    많이 나온 무리(1,470개, 4회)보다 **머릿수 비율보다 더 자주** 뽑혀야 한다.
    """
    random.seed(20260814)
    low_n, high_n, trials = 533, 1470, 4000
    def race(count, n):
        return min(-math.log(1 - random.random()) * (count + 1) for _ in range(n))
    wins = sum(1 for _ in range(trials) if race(3, low_n) < race(4, high_n))
    share = low_n / (low_n + high_n)          # 머릿수만 보면 26.6%
    assert share < wins / trials < 0.60       # 유리하되 독식하지는 않는다
