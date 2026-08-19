"""안 풀고 시간을 넘긴 문제는 **틀린 것으로 세어야** 한다 (2026-08-18).

무엇이 문제였나
---------------
문제 하나는 60초만 산다(`CHALLENGE_TTL_SECONDS`). 그런데 시간이 지나 버려진 문제는
**아무 데도 안 남았다.** 실패 횟수는 `captcha_attempts` 의 오답 행으로 세는데, 안 풀고
넘긴 시도는 그 행을 만들지 않기 때문이다.

그래서 **문제를 받고 그냥 놔두면 벌이 없었다.** 사람은 새 문제를 받아 다시 풀지만,
봇은 마음에 안 드는 문제를 무한히 넘겨도 실패 횟수가 0 이었다. 대기·차단은 실패를
세어 걸리는데, 세는 대상에 이 경우가 빠져 있었다.

이 시험이 지키는 것
-------------------
  1. 만료된 문제가 실패 행으로 기록된다
  2. **두 번 세지 않는다** — 상태를 옮겨 다음 훑기에 안 걸리게 한다
  3. `failure_reason='expired'` 로 남는다 — 진짜 오답과 섞이면 나중에 "사람 오답률"
     을 잴 때 안 푼 것까지 오답으로 세게 된다
  4. 아직 살아 있는 문제는 건드리지 않는다
"""

from __future__ import annotations

from app.config import settings
from app.db import Database


class _FakeCursor:
    """만료된 문제 두 건이 있는 상태를 흉내낸다."""

    def __init__(self, stale_ids: list[str]) -> None:
        self.statements: list[tuple[str, tuple]] = []
        self._stale = [{"id": i} for i in stale_ids]
        self._answer: object = []

    def execute(self, sql: str, args: tuple = ()) -> None:
        self.statements.append((sql, args))
        low = " ".join(sql.lower().split())
        if low.startswith("select id from captcha_challenges_v2"):
            self._answer = self._stale
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


class _FakeCache:
    enabled = True

    def __init__(self) -> None:
        self.bumps: list[tuple[str, str]] = []

    def bump(self, key: str, value: str) -> None:
        self.bumps.append((key, value))


def _run(stale_ids: list[str]) -> tuple[int, _FakeCursor, _FakeCache]:
    cursor = _FakeCursor(stale_ids)
    cache = _FakeCache()
    database = Database.__new__(Database)
    database.settings = settings
    database.cache = cache  # type: ignore[assignment]
    database.connection = lambda *a, **k: _FakeConnection(cursor)  # type: ignore[method-assign]
    recorded = database.expire_stale_challenges("sess-1")
    return recorded, cursor, cache


def _flat(sql: str) -> str:
    return " ".join(sql.lower().split())


def test_만료된_문제가_실패로_기록된다():
    recorded, cursor, _ = _run(["c-1", "c-2"])
    assert recorded == 2
    inserts = [s for s, _ in cursor.statements if "insert into captcha_attempts" in _flat(s)]
    assert len(inserts) == 2, "만료된 문제마다 실패 행이 하나씩 나와야 합니다"


def test_진짜_오답과_구분되게_남는다():
    """★`failure_reason='expired'`.

    구분이 없으면 "사람 오답률 12.8%" 같은 값에 **안 푼 것까지** 섞여 들어간다.
    """
    _, cursor, _ = _run(["c-1"])
    inserts = [s for s, _ in cursor.statements if "insert into captcha_attempts" in _flat(s)]
    assert inserts and "'expired'" in _flat(inserts[0])


def test_두_번_세지_않는다():
    """상태를 'issued' 밖으로 옮겨야 다음 훑기에 안 걸린다."""
    _, cursor, _ = _run(["c-1"])
    updates = [s for s, _ in cursor.statements if "update captcha_challenges_v2" in _flat(s)]
    assert updates, "상태를 옮기는 갱신이 없습니다 — 매번 다시 세게 됩니다"
    assert "status='expired'" in _flat(updates[0])


def test_살아_있는_문제는_건드리지_않는다():
    """조회 조건에 `status='issued'` 와 만료 시각이 둘 다 있어야 한다."""
    _, cursor, _ = _run([])
    selects = [s for s, _ in cursor.statements if _flat(s).startswith("select id from captcha_challenges_v2")]
    assert selects, "만료된 문제를 찾는 질의가 없습니다"
    flat = _flat(selects[0])
    assert "status='issued'" in flat
    assert "expires_at<=utc_timestamp(6)" in flat


def test_만료가_없으면_아무것도_안_한다():
    recorded, cursor, cache = _run([])
    assert recorded == 0
    assert not [s for s, _ in cursor.statements if "insert into captcha_attempts" in _flat(s)]
    assert not cache.bumps


def test_세션_실패_카운터가_올라간다():
    """대기·차단은 이 카운터로 걸린다. 안 올리면 기록만 남고 벌은 없다."""
    _, _, cache = _run(["c-1", "c-2"])
    assert cache.bumps == [("session_failures_10m", "sess-1")] * 2
