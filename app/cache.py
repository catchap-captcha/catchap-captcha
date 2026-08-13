# -*- coding: utf-8 -*-
"""Valkey(캐시) — 레이트리밋 카운터.

★설계의 핵심은 「캐시가 죽으면 어떻게 하나」다.

    레이트리밋   ★DB 질의로 되돌아간다
                 못 세면 무제한 허용이 된다. 느려도 막아야 한다.
                 ★「캐시가 죽으면 통과시킨다」는 절대 안 된다 —
                   캐시를 죽이는 것이 곧 레이트리밋 우회가 된다.

★1단계는 ★판정을 바꾸지 않는다. 캐시에 세면서 DB 질의도 그대로 돌리고
  값이 얼마나 다른지 로그로만 남긴다(mode=compare). 값이 미묘하게 다르기 때문이다.

    DB    "진짜 지난 10분"   created_at > now - 10분
    캐시  "이번 10분 창"     키가 만들어진 시각부터 600초

  경계에서 캐시 쪽이 느슨해질 수 있다. 며칠 숫자를 보고 한도를 정한 뒤에
  mode=cache 로 바꾼다. (0812 설계안 6절 · 민서님 ①번 답변)

⚠️`VALKEY_HOST` 가 비어 있으면 이 모듈은 아무 일도 하지 않는다.
  로컬 개발과 시험은 지금까지와 똑같이 돈다.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from .config import Settings

logger = logging.getLogger(__name__)

# 창 길이(초) — 이름이 곧 뜻이 되게 키에도 그대로 쓴다.
WINDOW_SECONDS = {
    "ip_challenges_1m": 60,
    "session_challenges_10m": 600,
    "session_failures_10m": 600,
    "session_telemetry_failures_10m": 600,
}

# 카운터 이름 → 키 모양. {v} 에 세션 id 나 ip 해시가 들어간다.
# 접두어 `cc:` 는 설정에서 붙인다 (민서님 ⑤번 답변).
KEY_SHAPE = {
    "ip_challenges_1m": "rl:ip:{v}:1m",
    "session_challenges_10m": "rl:sess:{v}:10m",
    "session_failures_10m": "rl:fail:{v}:10m",
    "session_telemetry_failures_10m": "rl:tele:{v}:10m",
}


class Cache:
    """Valkey 연결 하나를 감싼다. ★어떤 오류도 밖으로 던지지 않는다."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: Any | None = None
        self._lock = threading.Lock()
        self._warned: set[str] = set()
        # ★연결에 실패하면 잠시 쉰다. 안 그러면 캐시가 죽었을 때 ★요청마다
        #   연결을 다시 시도해 오히려 느려진다(연결 제한시간 × 카운터 수).
        self._retry_after = 0.0

    # ── 연결 ────────────────────────────────────────────────────────
    @property
    def enabled(self) -> bool:
        return bool(self.settings.valkey_host) and self.settings.valkey_rate_limit_mode != "off"

    def _connect(self) -> Any | None:
        try:
            import redis  # Valkey 는 Redis 규약을 그대로 쓴다
        except ImportError:
            self._warn_once("import", "redis 모듈이 없습니다 — DB 로만 셉니다")
            return None
        s = self.settings
        kwargs: dict[str, Any] = {
            "host": s.valkey_host,
            "port": s.valkey_port,
            "socket_timeout": s.valkey_timeout_seconds,
            "socket_connect_timeout": s.valkey_timeout_seconds,
            "decode_responses": True,
            "health_check_interval": 30,
        }
        if s.valkey_username:
            kwargs["username"] = s.valkey_username
        if s.valkey_password:
            kwargs["password"] = s.valkey_password
        if s.valkey_tls:
            kwargs["ssl"] = True
            if s.valkey_tls_ca_file:
                kwargs["ssl_ca_certs"] = s.valkey_tls_ca_file
            # ⚠️★호스트이름 검증은 끈다 — 켜면 연결이 실패한다(0813 파드에서 실측).
            #
            #   카카오 MemStore 인증서에는 ★SAN 이 없고 CN 이
            #   service.kakaoenterprise.com 이라, 엔드포인트 이름과 맞지 않는다.
            #   파이썬 ssl 을 직접 쓰면 server_hostname 에 CN 을 줘서 통과시킬 수
            #   있지만, ★redis-py 에는 그 인자를 넘길 자리가 없다.
            #
            #   ★CA 검증은 그대로 켜져 있다 — 이 CA 가 서명하지 않은 인증서는
            #   여전히 거부된다. 중간자는 막히고, "이름이 맞는지"만 못 본다.
            kwargs["ssl_check_hostname"] = False
        try:
            client = redis.Redis(**kwargs)
            client.ping()
            logger.info("[캐시] Valkey 에 붙었습니다 (mode=%s, prefix=%s)",
                        s.valkey_rate_limit_mode, s.valkey_key_prefix)
            return client
        except Exception as exc:
            self._warn_once("connect", f"연결 실패 — DB 로만 셉니다: {exc}")
            return None

    def _get(self) -> Any | None:
        if not self.enabled:
            return None
        if self._client is not None:
            return self._client
        if time.monotonic() < self._retry_after:
            return None                      # ★쉬는 중 — 조용히 DB 로 간다
        with self._lock:
            if self._client is None and time.monotonic() >= self._retry_after:
                self._client = self._connect()
                if self._client is None:
                    self._retry_after = time.monotonic() + self.settings.valkey_retry_seconds
        return self._client

    def drop(self) -> None:
        """쓰다가 실패했을 때 연결을 버린다 — 다음 요청이 새로 붙게."""
        self._client = None
        self._retry_after = time.monotonic() + self.settings.valkey_retry_seconds

    def _warn_once(self, key: str, message: str) -> None:
        """같은 경고로 로그를 채우지 않는다 — 챌린지마다 찍히면 로그를 못 쓰게 된다."""
        if key not in self._warned:
            logger.warning("[캐시] %s", message)
            self._warned.add(key)

    def _key(self, name: str, value: str) -> str:
        return self.settings.valkey_key_prefix + KEY_SHAPE[name].format(v=value)

    # ── 세기 ────────────────────────────────────────────────────────
    def bump(self, name: str, value: str) -> None:
        """카운터 하나를 올린다. ★실패해도 조용히 넘어간다(1단계 판정은 DB 가 한다)."""
        client = self._get()
        if client is None or not value:
            return
        try:
            key = self._key(name, value)
            pipe = client.pipeline()
            pipe.incr(key)
            # ★만료는 ★없을 때만 건다(nx). 매번 새로 걸면 창이 계속 미끄러져
            #   "10분 안에 N번" 이 "마지막 요청부터 10분" 이 되어 버린다.
            pipe.expire(key, WINDOW_SECONDS[name], nx=True)
            pipe.execute()
        except Exception as exc:
            self._warn_once("bump", f"카운터 증가 실패 — DB 로만 셉니다: {exc}")
            self.drop()

    def read(self, names_values: dict[str, str]) -> dict[str, int] | None:
        """여러 카운터를 한 번에 읽는다. ★하나라도 실패하면 None(=DB 를 쓰라)."""
        client = self._get()
        if client is None:
            return None
        try:
            names = list(names_values)
            raw = client.mget([self._key(n, names_values[n]) for n in names])
            return {n: int(v or 0) for n, v in zip(names, raw)}
        except Exception as exc:
            self._warn_once("read", f"카운터 읽기 실패 — DB 값을 씁니다: {exc}")
            self.drop()
            return None

    def ping(self) -> bool:
        client = self._get()
        if client is None:
            return False
        try:
            return bool(client.ping())
        except Exception:
            return False


def compare_and_log(db_values: dict[str, int], cache_values: dict[str, int] | None) -> None:
    """1단계 — 두 값을 견주어 ★다를 때만 로그로 남긴다.

    ★이 로그가 「한도를 얼마로 바꿔야 하나」의 근거가 된다.
    같을 때도 찍으면 로그가 넘쳐서 정작 다른 것을 못 본다.
    """
    if not cache_values:
        return
    다른것 = {
        name: (db_values.get(name, 0), cache_values.get(name, 0))
        for name in db_values
        if db_values.get(name, 0) != cache_values.get(name, 0)
    }
    if 다른것:
        logger.info("[캐시비교] DB 와 캐시가 다릅니다 (DB, 캐시): %s", 다른것)
