# -*- coding: utf-8 -*-
"""만료 토큰 정리 — ★살아 있는 토큰을 지우면 사용자가 캡차를 다시 풀어야 한다.

⚠️잡으려는 사고
  ① 만료 조건을 잘못 써서 ★아직 유효한 토큰을 지운다
  ② 한 번에 다 지우려 해 ★잠금이 길어지고 토큰 발급이 밀린다
  ③ 지울 것이 없는데 계속 도는다
"""
import dataclasses

from app.db import Database


def _settings(**kw):
    from app.config import Settings
    return dataclasses.replace(Settings(), **kw)


class 가짜커서:
    def __init__(self, 남은행수):
        self.남은 = 남은행수
        self.실행됨 = []
        self.rowcount = 0

    def execute(self, sql, args=None):
        self.실행됨.append((" ".join(sql.split()), args))
        한번에 = args[1] if args and len(args) > 1 else 1000
        self.rowcount = min(self.남은, 한번에)
        self.남은 -= self.rowcount

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class 가짜연결:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _db(monkeypatch, 남은행수, **kw):
    cur = 가짜커서(남은행수)
    d = Database(_settings(**kw))
    from contextlib import contextmanager

    @contextmanager
    def conn(readonly=False):
        yield 가짜연결(cur)

    monkeypatch.setattr(d, "connection", conn)
    return d, cur


def test_만료된_것만_지운다(monkeypatch):
    d, cur = _db(monkeypatch, 10, token_retention_hours=24, token_purge_batch=1000)
    d.purge_expired_tokens()
    sql, args = cur.실행됨[0]
    assert "DELETE FROM captcha_tokens" in sql
    # ★조건이 빠지면 표 전체가 날아간다
    assert "expires_at <" in sql, "★만료 조건이 없다 — 살아 있는 토큰까지 지운다"
    assert "UTC_TIMESTAMP" in sql, "서버 지역시간을 쓰면 시간대 차이로 잘못 지운다"
    assert args[0] == 24, "여유 시간이 설정대로 안 들어갔다"


def test_나눠서_지운다(monkeypatch):
    """★한 번에 다 지우면 잠금이 길어져 토큰 발급이 밀린다."""
    d, cur = _db(monkeypatch, 2500, token_purge_batch=1000)
    n = d.purge_expired_tokens()
    assert n == 2500
    assert len(cur.실행됨) == 3, f"1000씩 세 번이어야 하는데 {len(cur.실행됨)}번"
    for _, args in cur.실행됨:
        assert args[1] == 1000, "배치 크기가 안 지켜졌다"


def test_지울_것이_없으면_한_번만_돈다(monkeypatch):
    d, cur = _db(monkeypatch, 0)
    assert d.purge_expired_tokens() == 0
    assert len(cur.실행됨) == 1, "지울 것이 없는데 계속 돈다"


def test_무한루프를_막는다(monkeypatch):
    """DELETE 가 계속 배치만큼 지워도 max_batches 에서 멈춰야 한다."""
    d, cur = _db(monkeypatch, 10**9, token_purge_batch=10)
    d.purge_expired_tokens(max_batches=5)
    assert len(cur.실행됨) == 5
