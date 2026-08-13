# -*- coding: utf-8 -*-
"""레이트리밋 캐시 — ★캐시가 죽어도 막는 힘이 사라지지 않는지 본다.

⚠️잡으려는 사고
  ① 캐시가 죽었는데 카운터가 0 으로 읽혀 ★무제한 허용이 된다
     → 캐시를 죽이는 것이 곧 레이트리밋 우회가 된다. 제일 위험하다.
  ② 캐시 오류가 밖으로 튀어나와 ★챌린지 발급 자체가 실패한다
  ③ 만료(EXPIRE)를 매번 새로 걸어 창이 계속 미끄러진다
     → "10분에 N번" 이 "마지막 요청부터 10분" 이 되어 영원히 안 풀린다
"""
import dataclasses

import pytest

from app.cache import Cache, compare_and_log


def _settings(**kw):
    from app.config import Settings
    return dataclasses.replace(Settings(), **kw)


class 가짜파이프:
    def __init__(self, 기록):
        self.기록 = 기록

    def incr(self, key):
        self.기록.append(("incr", key))
        return self

    def expire(self, key, seconds, nx=False):
        self.기록.append(("expire", key, seconds, nx))
        return self

    def execute(self):
        return [1, True]


class 가짜클라이언트:
    def __init__(self, 값=None, 터짐=False):
        self.값, self.터짐, self.기록 = 값 or {}, 터짐, []

    def pipeline(self):
        if self.터짐:
            raise RuntimeError("캐시 죽음")
        return 가짜파이프(self.기록)

    def mget(self, keys):
        if self.터짐:
            raise RuntimeError("캐시 죽음")
        return [self.값.get(k) for k in keys]

    def ping(self):
        if self.터짐:
            raise RuntimeError("캐시 죽음")
        return True


def _cache(monkeypatch, client, **kw):
    s = _settings(valkey_host="valkey.example", valkey_key_prefix="cc:", **kw)
    c = Cache(s)
    monkeypatch.setattr(c, "_connect", lambda: client)
    return c


def test_호스트가_비면_아무것도_안_한다():
    c = Cache(_settings(valkey_host=""))
    assert c.enabled is False
    c.bump("ip_challenges_1m", "abc")          # 터지면 안 된다
    assert c.read({"ip_challenges_1m": "abc"}) is None


def test_모드가_off_면_안_쓴다():
    c = Cache(_settings(valkey_host="valkey.example", valkey_rate_limit_mode="off"))
    assert c.enabled is False


def test_카운터를_올리면_INCR_과_EXPIRE_가_같이_나간다(monkeypatch):
    client = 가짜클라이언트()
    c = _cache(monkeypatch, client)
    c.bump("session_challenges_10m", "sess-1")
    assert ("incr", "cc:rl:sess:sess-1:10m") in client.기록
    expire = [r for r in client.기록 if r[0] == "expire"][0]
    assert expire[2] == 600, "10분 창인데 만료가 다르다"
    # ★nx=True 여야 창이 안 미끄러진다
    assert expire[3] is True, "★EXPIRE 를 매번 새로 걸면 창이 계속 미끄러진다"


def test_캐시가_죽어도_예외가_밖으로_안_나온다(monkeypatch):
    c = _cache(monkeypatch, 가짜클라이언트(터짐=True))
    c.bump("ip_challenges_1m", "ip-1")          # ★터지면 챌린지 발급이 실패한다
    assert c.read({"ip_challenges_1m": "ip-1"}) is None


def test_캐시가_죽으면_None_을_준다_0이_아니다(monkeypatch):
    """★가장 중요한 시험 — 0 을 주면 '한 번도 안 왔다'가 되어 무제한 허용이 된다."""
    c = _cache(monkeypatch, 가짜클라이언트(터짐=True))
    got = c.read({"ip_challenges_1m": "ip-1", "session_challenges_10m": "s-1"})
    assert got is None, "★캐시가 죽었는데 숫자를 돌려주면 레이트리밋이 뚫린다"
    assert got != {"ip_challenges_1m": 0, "session_challenges_10m": 0}


def test_읽은_값을_이름별로_돌려준다(monkeypatch):
    client = 가짜클라이언트({"cc:rl:ip:ip-1:1m": "7", "cc:rl:sess:s-1:10m": None})
    c = _cache(monkeypatch, client)
    got = c.read({"ip_challenges_1m": "ip-1", "session_challenges_10m": "s-1"})
    assert got == {"ip_challenges_1m": 7, "session_challenges_10m": 0}


def test_비교로그는_다를_때만_남긴다(caplog):
    import logging
    caplog.set_level(logging.INFO, logger="app.cache")
    compare_and_log({"a": 1, "b": 2}, {"a": 1, "b": 2})
    assert "캐시비교" not in caplog.text, "같은데도 로그를 남기면 로그가 넘친다"
    compare_and_log({"a": 1, "b": 2}, {"a": 1, "b": 5})
    assert "캐시비교" in caplog.text
    assert "'b': (2, 5)" in caplog.text


def test_캐시가_없어도_비교로그가_안_터진다():
    compare_and_log({"a": 1}, None)      # 예외가 나면 안 된다


def test_연결이_실패하면_쉬었다가_다시_시도한다(monkeypatch):
    """★캐시가 죽었을 때 요청마다 연결을 다시 걸면 캐시 없을 때보다 느려진다.

    연결 제한시간 0.3초 × 카운터 3개 = 챌린지마다 최대 0.9초가 더 붙는다.
    그래서 실패하면 잠시 쉬고, 쉬는 동안에는 조용히 DB 로 간다.
    """
    시도 = []

    c = Cache(_settings(valkey_host="valkey.example", valkey_retry_seconds=60))

    def 실패하는연결():
        시도.append(1)
        return None

    monkeypatch.setattr(c, "_connect", 실패하는연결)
    for _ in range(5):
        c.bump("ip_challenges_1m", "ip-1")
        c.read({"ip_challenges_1m": "ip-1"})
    assert len(시도) == 1, f"★쉬지 않고 매번 다시 붙으려 한다 ({len(시도)}번)"


def test_쓰다가_실패하면_연결을_버린다(monkeypatch):
    """죽은 연결을 그대로 들고 있으면 계속 같은 오류를 낸다."""
    client = 가짜클라이언트(터짐=True)
    c = _cache(monkeypatch, client, valkey_retry_seconds=60)
    assert c._get() is client
    c.read({"ip_challenges_1m": "ip-1"})
    assert c._client is None, "★실패한 연결을 그대로 들고 있다"
