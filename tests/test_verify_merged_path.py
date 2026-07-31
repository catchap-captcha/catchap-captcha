"""verify() 통합 경로 회귀 테스트 — PoW 병합 이후.

병합 전까지 verify() 를 호출하는 테스트는 중복선택 422 하나뿐이었고, 그건 PoW·행동
채점·게이트 어느 것에도 도달하지 않는다. 즉 verify 본체는 무검증 상태였다.

여기서 고정하려는 것은 세 가지다.

1. 모델에 넘어가는 궤적이 **서버가 저장한 배치**여야 한다. origin/ms 의 그림자 채점은
   payload.events(클라이언트가 보낸 것)를 넘기고 있었다. 그 경로로 돌아가면 봇이
   그럴듯한 궤적을 만들어 보내는 것만으로 사람 점수를 받는다. 조용히 되돌아가기
   쉬운 종류의 퇴행이라 테스트로 못박는다.
2. PoW 미해결 요청이 채점 이전에 막혀야 한다.
3. 저장된 배치가 없거나 lifecycle 이 깨지면 모델을 호출하지 않고 사유가 남아야 한다.
"""

from __future__ import annotations

import dataclasses
import hashlib
from datetime import timedelta

import pytest

from app import main
from app.behavior_client import BehaviorPrediction

CHALLENGE_ID = "challenge-merged-1"
SESSION_ID = "session-merged-1"
TARGET = "tmp_target"


def _solve_pow(seed: str, bits: int) -> str:
    """테스트용 실제 PoW 해. bits 를 낮게 잡아 즉시 끝난다."""
    for candidate in range(1 << 24):
        nonce = str(candidate)
        digest = hashlib.sha256(f"{seed}:{nonce}".encode()).digest()
        if main._leading_zero_bits(digest) >= bits:
            return nonce
    raise AssertionError("PoW 해를 찾지 못했다")


def _server_events() -> list[dict[str, object]]:
    """lifecycle·좌표결속을 통과하는 최소 궤적. press 가 정답 객체 안에서 시작한다."""
    base = 1_700_000_000_000
    # 좌표결속은 press 뒤에 drag_start → drop → selection_add 순서를 요구한다.
    rows: list[tuple[str, float | None, float | None, int]] = [
        ("challenge_loaded", None, None, 0),
        ("pointer_down", 0.20, 0.30, 400),
        ("drag_start", 0.20, 0.30, 420),
    ]
    for step in range(1, 7):
        rows.append(("pointer_move", 0.20 + 0.09 * step, 0.30 + 0.08 * step,
                     420 + 120 * step))
    rows += [
        ("drop", 0.84, 0.80, 1300),
        ("selection_add", None, None, 1320),
        ("submit", None, None, 1500),
    ]
    return [
        {"seq": seq, "type": kind,
         "object_id": None if kind in ("challenge_loaded", "submit") else TARGET,
         "x": x, "y": y, "timestamp_ms": base + offset}
        for seq, (kind, x, y, offset) in enumerate(rows)
    ]


@pytest.fixture
def stub(monkeypatch, tmp_path):
    """verify() 가 닿는 DB·모델 호출을 전부 대체하고 넘겨진 인자를 기록한다."""
    captured: dict[str, object] = {}

    # Settings 는 frozen dataclass 라 필드 대입이 안 된다. 복제해서 갈아끼운다.
    monkeypatch.setattr(main, "settings", dataclasses.replace(
        main.settings,
        runtime_dir=tmp_path,
        behavior_event_transport="shadow",
        behavior_policy_mode="shadow",
        pow_enabled=True,
        pow_difficulty_bits=8,
    ))

    monkeypatch.setattr(main.database, "challenge_for_verify", lambda _cid: {
        "session_id": SESSION_ID,
        "status": "issued",
        "expires_at": main.utcnow() + timedelta(minutes=5),
        "attempt_count": 0,
        "question_id": "question-1",
        "purpose": "signup",
        "lecture_id": None,
        "created_at": main.utcnow() - timedelta(seconds=5),
        "client_ip_hash": "iphash",
        "objects": [
            {"temporary_object_id": TARGET, "role": "target",
             "bbox_x": 0.10, "bbox_y": 0.20, "bbox_width": 0.30, "bbox_height": 0.30},
            {"temporary_object_id": "tmp_other", "role": "distractor",
             "bbox_x": 0.60, "bbox_y": 0.10, "bbox_width": 0.20, "bbox_height": 0.20},
        ],
    })
    monkeypatch.setattr(main.database, "get_question",
                        lambda _qid: {"image_width": 500, "image_height": 500})
    monkeypatch.setattr(main.database, "trusted_behavior_events",
                        lambda _cid: (_server_events(), None))
    monkeypatch.setattr(main.database, "behavior_batch_received_at", lambda _cid: [])
    monkeypatch.setattr(main.database, "request_pattern", lambda *_a: {
        "ip_challenges_1m": 1, "session_challenges_10m": 1,
        "session_failures_10m": 0, "session_telemetry_failures_10m": 0,
    })
    monkeypatch.setattr(main.database, "record_attempt", lambda *_a, **_k: "attempt-1")
    monkeypatch.setattr(main.database, "record_behavior_shadow_prediction",
                        lambda *_a, **_k: None)
    monkeypatch.setattr(main.database, "record_fingerprint", lambda *_a, **_k: None)
    monkeypatch.setattr(main.database, "signature_cluster_size", lambda *_a, **_k: 1)
    monkeypatch.setattr(main.database, "create_token", lambda *_a, **_k: None)

    def fake_score(payload, attempt_id, reason):
        captured["payload"] = payload
        captured["reason"] = reason
        if payload is None:
            return BehaviorPrediction(attempt_id, "unavailable", reason)
        return BehaviorPrediction(attempt_id, "scored", None, risk_score=5.0,
                                  risk_level="low", recommended_action="allow",
                                  policy_mode="shadow", human_score=0.95)

    monkeypatch.setattr(main.behavior_ai, "score", fake_score)
    monkeypatch.setattr(main.behavior_ai, "record_shadow_outcome",
                        lambda prediction, _verdict: prediction)
    return captured


class _FakeClient:
    host = "203.0.113.9"


class _FakeRequest:
    """client_ip() 와 check_origin() 이 만지는 만큼만 흉내낸다."""
    client = _FakeClient()
    headers: dict[str, str] = {}


def _verify(pow_nonce: str | None, events: list | None = None, selected: list | None = None):
    payload = main.VerifyRequest(
        selected_object_ids=selected or [TARGET],
        session_id=SESSION_ID,
        duration_ms=1500,
        events=events or [],
        client_signals={},
        pow_nonce=pow_nonce,
    )
    return main.verify(CHALLENGE_ID, payload, _FakeRequest(), main.settings.site_key)


def test_model_scores_server_stored_batches_not_client_submitted_events(stub):
    """클라이언트가 보낸 궤적은 채점에 쓰이지 않는다."""
    forged = [
        {"type": "pointer_down", "object_id": TARGET, "x": 0.99, "y": 0.99,
         "timestamp_ms": 1},
        {"type": "pointer_move", "object_id": TARGET, "x": 0.98, "y": 0.98,
         "timestamp_ms": 2},
    ]

    result = _verify(_solve_pow(CHALLENGE_ID, 8), events=forged)

    assert result["success"] is True
    payload = stub["payload"]
    assert payload is not None, stub["reason"]
    # 서버 저장 궤적은 9개의 좌표 이벤트(challenge_loaded·selection_add·submit 제외).
    # 위조본을 채점했다면 2개가 된다.
    assert len(payload["events"]) > len(forged)
    # 좌표는 픽셀로 정규화돼 넘어간다(정규화값 × 500). 서버 저장 궤적의 최대 x 는
    # drop 지점 0.84 → 420px 이고, 위조본을 채점했다면 0.99 → 495px 가 나온다.
    xs = [event.get("x") for event in payload["events"]]
    assert max(xs) == pytest.approx(0.84 * 500, abs=1.0), xs
    assert all(x < 0.95 * 500 for x in xs), xs


def test_unsolved_pow_is_rejected_before_any_scoring(stub):
    result = _verify(pow_nonce=None)

    assert result == {"success": False, "pow_failed": True}
    assert "payload" not in stub, "PoW 실패 요청이 모델까지 갔다"


def test_missing_batches_skip_the_model_and_keep_the_reason(stub, monkeypatch):
    monkeypatch.setattr(main.database, "trusted_behavior_events",
                        lambda _cid: ([], "behavior_batches_missing"))

    result = _verify(_solve_pow(CHALLENGE_ID, 8))

    assert stub["payload"] is None
    assert stub["reason"] == "behavior_batches_missing"
    # 궤적이 아예 없으면 통과하지 않는다. shadow 전송 모드에서는 내 telemetry 승급이
    # 발동하지 않지만(active 에서만 step_up), 그 다음의 ms 규칙 게이트가 0 이벤트를
    # 높은 위험으로 채점해 잡는다. 두 층이 겹쳐 있는 셈이므로 그대로 고정한다.
    assert result["success"] is False


def test_honeypot_submission_blocks_before_the_model_is_called(stub, monkeypatch):
    """허니팟 게이트의 현재 동작을 고정한다 — 그리고 그 대가를 눈에 보이게 둔다.

    허니팟을 집었다는 건 확정 봇이다. 차단은 맞다. 다만 지금은 AI 채점 **이전에**
    return 하므로 그 궤적이 버려진다. 라벨이 확실한 봇 궤적은 우리가 가장 부족한
    데이터라, 채점 후 차단으로 순서를 바꾸면 사용자 영향 없이(어차피 봇이다)
    표본을 얻을 수 있다. 민서님 보안 게이트라 임의로 바꾸지 않고 고정만 해둔다.
    """
    challenge = main.database.challenge_for_verify(CHALLENGE_ID)
    trap = "tmp_honeypot"
    monkeypatch.setattr(main.database, "challenge_for_verify",
                        lambda _cid: {**challenge, "honeypot_ids": f'["{trap}"]'})

    result = _verify(_solve_pow(CHALLENGE_ID, 8), selected=[trap])

    assert result["blocked"] is True
    assert result["reason"] == "honeypot"
    assert "payload" not in stub, "허니팟 차단이 모델 호출 뒤에 일어났다"


def test_adaptive_pow_uses_the_bits_stored_on_the_challenge(stub, monkeypatch):
    """적응형 PoW: 검증은 발급 시 저장된 난이도를 쓴다(전역 기본값이 아니라)."""
    challenge = main.database.challenge_for_verify(CHALLENGE_ID)
    monkeypatch.setattr(main.database, "challenge_for_verify",
                        lambda _cid: {**challenge, "pow_bits": 12})

    # 전역 기본값(8)로 푼 해는 저장된 12비트를 만족하지 못한다.
    assert _verify(_solve_pow(CHALLENGE_ID, 8)) == {"success": False, "pow_failed": True}
    # 12비트로 풀면 통과한다.
    assert _verify(_solve_pow(CHALLENGE_ID, 12))["success"] is True


def test_participant_code_wins_over_session_id(stub):
    """?participant= 로 온 코드가 세션 대신 참여자 구분이 되어야 한다.

    수집 세션에서 한 사람이 탭을 바꿔가며 장치별 블록을 돌 때, 코드가 없으면
    탭마다 다른 사람으로 잡힌다. 그래서 코드가 오면 그쪽이 이겨야 한다.
    반대로 일반 사용자는 코드가 없으므로 session_id 폴백이 유지돼야 한다.
    """
    payload = main.VerifyRequest(
        selected_object_ids=[TARGET], session_id=SESSION_ID, duration_ms=1500,
        client_signals={}, pow_nonce=_solve_pow(CHALLENGE_ID, 8),
        participant_id="P07-mouse",
    )
    result = main.verify(CHALLENGE_ID, payload, _FakeRequest(), main.settings.site_key)

    assert result["success"] is True
    assert stub["payload"]["anonymous_participant_id"] == "P07-mouse"


def test_missing_participant_code_falls_back_to_session(stub):
    result = _verify(_solve_pow(CHALLENGE_ID, 8))

    assert result["success"] is True
    assert stub["payload"]["anonymous_participant_id"] == SESSION_ID
