"""행동 요약은 **언제나** JSON 으로 써질 수 있어야 한다.

2026-08-13 운영 장애의 회귀 시험. 대기 사다리(#32)가 `request_pattern` 에
`last_failure_at`(날짜)을 추가했는데, 그 딕셔너리가 요약에 통째로 실려 나가고
요약은 두 곳에서 `json.dumps` 된다(궤적 파일 기록·`record_attempt`).

증상이 고약했다 — 세션의 **첫** 시도는 아직 오답이 없어 값이 `None` 이라 통과하고,
**두 번째부터 전부 500** 이었다. 화면에는 "확인 처리 중 오류가 발생했습니다" 로만
보여서, 한 번 틀린 사람은 다시 풀 수가 없었다.

실측(운영, 2026-08-13): 매번 새 세션이면 3/3 성공, 같은 세션 2회차면 3/3 실패.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from app.main import summarize


def _pattern(last_failure_at: datetime | None) -> dict[str, object]:
    return {
        "ip_challenges_1m": 1,
        "session_challenges_10m": 2,
        "session_failures_10m": 1,
        "session_telemetry_failures_10m": 0,
        "session_suspicious_10m": 1,
        "last_failure_at": last_failure_at,
    }


def _summarize(pattern: dict[str, object]) -> dict:
    return summarize([], set(), set(), 800, False, pattern, False)


def test_오답이_있는_세션의_요약도_직렬화된다():
    """★이게 깨지면 두 번째 시도가 전부 500 이 된다."""
    summary = _summarize(_pattern(datetime(2026, 8, 13, 6, 30, tzinfo=timezone.utc)))
    json.dumps(summary, ensure_ascii=False)   # 여기서 터지면 운영이 터진다


def test_마지막_오답_시각이_요약에_남는다():
    """지우지 말고 문자열로 남긴다 — 대기를 언제부터 쟀는지 되짚을 수 있어야 한다."""
    when = datetime(2026, 8, 13, 6, 30, tzinfo=timezone.utc)
    summary = _summarize(_pattern(when))
    assert summary["request_pattern"]["last_failure_at"] == when.isoformat()


def test_첫_시도는_예전처럼_None():
    """오답이 없으면 값이 없다. 이 경우는 원래도 통과했으므로 동작이 안 바뀌어야 한다."""
    summary = _summarize(_pattern(None))
    assert summary["request_pattern"]["last_failure_at"] is None
    json.dumps(summary, ensure_ascii=False)
