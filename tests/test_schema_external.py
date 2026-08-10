# -*- coding: utf-8 -*-
"""`SCHEMA_MANAGED_EXTERNALLY` 가 실제로 DDL 을 멈추고, ★모자라면 막는지 확인한다.

⚠️잡으려는 사고 두 가지
  ① 스위치를 켰는데도 DDL 이 계속 나가면 → 앱 계정의 DDL 권한을 못 뗀다
  ② 스위치를 켠 뒤 표가 없는데도 조용히 뜨면 → 첫 요청에서 "그런 표 없음" 이 난다
     ★원인이 기동에서 멀어진다. 그래서 ★기동을 막아야 한다.
"""
import re

import pytest

from app.db import SCHEMA, Database

TABLES = [re.search(r"CREATE TABLE IF NOT EXISTS (\w+)", s).group(1) for s in SCHEMA]


class 가짜커서:
    def __init__(self, 표, 칼럼있음):
        self.표, self.칼럼있음, self.실행됨, self._결과 = 표, 칼럼있음, [], None

    def execute(self, sql, args=None):
        self.실행됨.append(sql.strip())
        low = sql.strip().lower()
        if low.startswith("show tables"):
            self._결과 = [{"t": t} for t in self.표]
        elif "information_schema.columns" in low:
            self._결과 = [{"n": 1 if self.칼럼있음 else 0}]
        elif "get_lock" in low:
            self._결과 = [{"acquired": 1}]
        else:
            self._결과 = [{}]

    def fetchall(self):
        return self._결과 or []

    def fetchone(self):
        return (self._결과 or [{}])[0]

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

    def rollback(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _db(monkeypatch, settings, 표, 칼럼있음=True):
    cur = 가짜커서(표, 칼럼있음)
    d = Database(settings)
    from contextlib import contextmanager

    @contextmanager
    def conn(readonly=False):
        yield 가짜연결(cur)

    monkeypatch.setattr(d, "connection", conn)
    return d, cur


def _settings(**kw):
    from app.config import Settings
    import dataclasses
    return dataclasses.replace(Settings(), **kw)


def test_스위치가_꺼져_있으면_예전처럼_DDL_을_한다(monkeypatch):
    d, cur = _db(monkeypatch, _settings(schema_managed_externally=False), TABLES)
    d.initialize()
    ddl = [s for s in cur.실행됨 if s.upper().startswith(("CREATE TABLE", "ALTER TABLE"))]
    assert len(ddl) >= len(SCHEMA), "기본값에서 DDL 이 줄었다 — 예전 동작이 바뀌면 안 된다"


def test_스위치를_켜면_DDL_을_한_줄도_안_한다(monkeypatch):
    d, cur = _db(monkeypatch, _settings(schema_managed_externally=True), TABLES)
    d.initialize()
    ddl = [s for s in cur.실행됨 if s.upper().startswith(("CREATE TABLE", "ALTER TABLE", "CREATE INDEX"))]
    assert ddl == [], f"★DDL 이 나갔다: {ddl[:2]}"


def test_표가_모자라면_기동을_막는다(monkeypatch):
    d, _ = _db(monkeypatch, _settings(schema_managed_externally=True), TABLES[:-1])
    with pytest.raises(RuntimeError) as e:
        d.initialize()
    assert TABLES[-1] in str(e.value), "어느 표가 없는지 말해 줘야 한다"


def test_칼럼이_모자라면_기동을_막는다(monkeypatch):
    d, _ = _db(monkeypatch, _settings(schema_managed_externally=True), TABLES, 칼럼있음=False)
    with pytest.raises(RuntimeError) as e:
        d.initialize()
    assert "captcha_challenges_v2.lecture_id" in str(e.value)
