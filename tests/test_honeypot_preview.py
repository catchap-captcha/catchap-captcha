"""함정이 미리보기 하나로 들통나지 않아야 한다 (2026-08-13).

함정은 객체 표에 안 들어가서 미리보기가 **항상 404** 였다. 진짜 객체는 35,508개 전부
조각이 있으므로 `404 = 함정` 이 예외 없이 성립했고, 봇은 문제를 풀기도 전에 캡차 시도를
한 번도 쓰지 않고 함정을 전부 걸러낼 수 있었다(실측: 10문항 3개씩 전부 적중, 문항당 1.2초).

걸러내면 함정을 1→3개로 늘린 효과(찍기 성공률 33%→20%)가 통째로 사라진다.

여기서는 두 가지를 본다.
  ① 함정에도 **진짜 조각**이 나간다 — 404 라는 단서가 없어진다
  ② 서로 다른 함정을 여럿 받아가면 **훑은 것으로 보고 막는다**
"""

from __future__ import annotations

from app.cache import HONEYPOT_PROBE_KEY, HONEYPOT_PROBE_WINDOW, KEY_SHAPE
from app.config import settings
from app.db import Database


class _FakeCursor:
    """`decoy_piece_path` 가 던지는 두 질의에만 답한다."""

    def __init__(self, total: int, rows: dict[int, str]) -> None:
        self.total, self.rows, self._answer = total, rows, None
        self.offsets: list[int] = []

    def execute(self, sql: str, args: tuple = ()) -> None:
        if "COUNT(*)" in sql:
            self._answer = {"n": self.total}
            return
        offset = args[1]
        self.offsets.append(offset)
        path = self.rows.get(offset)
        self._answer = {"piece_path": path} if path else None

    def fetchone(self):
        return self._answer

    def __enter__(self): return self
    def __exit__(self, *exc): return False


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None: self._cursor = cursor
    def cursor(self): return self._cursor
    def __enter__(self): return self
    def __exit__(self, *exc): return False


def _database(cursor: _FakeCursor) -> Database:
    database = Database.__new__(Database)
    database._piece_total = None
    database.connection = lambda *a, **k: _FakeConnection(cursor)  # type: ignore[method-assign]
    return database


def test_함정도_진짜_조각을_받는다():
    """★없으면 404 가 곧 '이게 함정이다' 라는 표시가 된다."""
    cursor = _FakeCursor(total=100, rows={i: f"pieces/{i}.png" for i in range(100)})
    assert _database(cursor).decoy_piece_path("q-1", "tmp_abc") is not None


def test_같은_함정은_늘_같은_그림():
    """다시 물을 때마다 그림이 바뀌면 그 자체가 새 단서가 된다(캐시·재요청)."""
    rows = {i: f"pieces/{i}.png" for i in range(100)}
    first = _database(_FakeCursor(100, rows)).decoy_piece_path("q-1", "tmp_abc")
    second = _database(_FakeCursor(100, rows)).decoy_piece_path("q-1", "tmp_abc")
    assert first == second


def test_함정마다_다른_그림():
    """전부 같은 그림이면 '이 그림 = 함정' 으로 또 갈린다."""
    rows = {i: f"pieces/{i}.png" for i in range(100)}
    picks = {_database(_FakeCursor(100, rows)).decoy_piece_path("q-1", f"tmp_{i}")
             for i in range(12)}
    assert len(picks) > 1


def test_고른_자리가_조각_수_안에_든다():
    """씨앗을 그대로 쓰면 범위를 벗어나 아무것도 안 나온다."""
    cursor = _FakeCursor(total=7, rows={i: f"pieces/{i}.png" for i in range(7)})
    _database(cursor).decoy_piece_path("q-1", "tmp_" + "z" * 40)
    assert cursor.offsets and all(0 <= offset < 7 for offset in cursor.offsets)


def test_조각이_하나도_없으면_없다고_한다():
    """빈 DB 에서 엉뚱한 경로를 지어내면 안 된다."""
    assert _database(_FakeCursor(total=0, rows={})).decoy_piece_path("q-1", "tmp_a") is None


def test_훑기_키는_레이트리밋_카운터와_섞이지_않는다():
    """★섞으면 집합을 카운터로 읽다 실패해 카운터 비교가 통째로 죽는다.

    `db.CACHED_COUNTERS` 가 `KEY_SHAPE` 를 그대로 가져다 MGET 으로 읽기 때문이다.
    """
    assert "honeypot_probes" not in KEY_SHAPE
    assert HONEYPOT_PROBE_KEY and HONEYPOT_PROBE_WINDOW >= settings.challenge_ttl_seconds


def test_둘_이상_받아가면_막는다():
    """하나는 실수로 집었을 수 있다. 둘은 안 보이는 덫을 두 개 집었다는 뜻이다."""
    assert settings.honeypot_probe_block == 2
