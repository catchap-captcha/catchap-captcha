from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import main
from app.db import _payload_hash, _receipt_hash, _receipt_timestamp, _validate_behavior_batches
from app.main import (
    BehaviorBatchRequest,
    BehaviorEvent,
    detect_stop_go_signal,
    detect_batch_delivery_timing,
    summarize,
    validate_behavior_action_binding,
    validate_behavior_lifecycle,
    trusted_duration_ms,
)


def _event(seq: int) -> dict[str, object]:
    return {
        "seq": seq,
        "type": "pointer_move",
        "object_id": "tmp_object",
        "x": 0.25,
        "y": 0.75,
        "timestamp_ms": 1_700_000_000_000 + seq,
    }


def test_batch_schema_requires_a_bounded_event_sequence():
    batch = BehaviorBatchRequest.model_validate(
        {
            "session_id": "session-123",
            "nonce": "n" * 32,
            "batch_seq": 0,
            "previous_receipt": None,
            "events": [_event(0), _event(1)],
        }
    )

    assert [event.seq for event in batch.events] == [0, 1]

    invalid = _event(0)
    invalid.pop("seq")
    with pytest.raises(ValidationError):
        BehaviorBatchRequest.model_validate(
            {
                "session_id": "session-123",
                "nonce": "n" * 32,
                "batch_seq": 0,
                "events": [invalid],
            }
        )


def test_receipt_is_stable_for_the_stored_batch_and_receive_time():
    events = [_event(0), _event(1)]
    received_at = datetime(2026, 7, 24, 3, 15, 30, 123456, tzinfo=timezone.utc)
    payload_hash = _payload_hash(events)

    receipt = _receipt_hash("challenge-1", 0, None, payload_hash, received_at)

    assert _receipt_timestamp(received_at) == "2026-07-24T03:15:30.123456Z"
    assert receipt == _receipt_hash("challenge-1", 0, None, payload_hash, received_at)
    assert receipt != _receipt_hash("challenge-1", 1, None, payload_hash, received_at)
    assert receipt != _receipt_hash("challenge-1", 0, "previous", payload_hash, received_at)


def test_saved_batches_reject_a_broken_event_sequence():
    events = [_event(0), _event(1)]
    received_at = datetime(2026, 7, 24, 3, 15, 30, 123456, tzinfo=timezone.utc)
    payload_hash = _payload_hash(events)
    receipt = _receipt_hash("challenge-1", 0, None, payload_hash, received_at)
    session = {"next_batch_seq": 1, "last_receipt_hash": receipt, "received_event_count": 2}
    row = {
        "batch_seq": 0,
        "event_count": 2,
        "previous_receipt_hash": None,
        "payload_hash": payload_hash,
        "receipt_hash": receipt,
        "events_json": events,
        "received_at": received_at,
    }

    trusted, reason = _validate_behavior_batches("challenge-1", session, [row])
    assert reason is None
    assert trusted == events

    row["events_json"] = [{**events[0], "seq": 3}, events[1]]
    trusted, reason = _validate_behavior_batches("challenge-1", session, [row])
    assert trusted == []
    assert reason == "behavior_event_sequence_invalid"


def test_server_batch_records_are_converted_before_summary_calculation():
    raw_events = [
        {"seq": 0, "type": "challenge_loaded", "object_id": None, "x": None, "y": None, "timestamp_ms": 100},
        {"seq": 1, "type": "pointer_down", "object_id": "tmp_object", "x": 0.1, "y": 0.1, "timestamp_ms": 700},
        {"seq": 2, "type": "drag_start", "object_id": "tmp_object", "x": 0.1, "y": 0.1, "timestamp_ms": 700},
        {"seq": 3, "type": "pointer_move", "object_id": "tmp_object", "x": 0.5, "y": 0.5, "timestamp_ms": 850},
        {"seq": 4, "type": "drop", "object_id": "tmp_object", "x": 0.8, "y": 0.8, "timestamp_ms": 1000},
    ]

    summary = summarize(
        [BehaviorEvent.model_validate(event) for event in raw_events],
        {"tmp_object"},
        {"tmp_object"},
        900,
        True,
        {"session_challenges_10m": 0, "session_failures_10m": 0, "ip_challenges_1m": 0},
        False,
    )

    assert summary["drag_count"] == 1
    assert summary["pointer_move_count"] == 1


def test_behavior_lifecycle_requires_a_complete_drag_before_submit():
    events = [
        {"type": "challenge_loaded", "timestamp_ms": 100},
        {"type": "pointer_down", "timestamp_ms": 500},
        {"type": "pointer_move", "timestamp_ms": 650},
        {"type": "drop", "timestamp_ms": 820},
        {"type": "selection_add", "timestamp_ms": 820},
        {"type": "submit", "timestamp_ms": 900},
    ]

    assert validate_behavior_lifecycle(events) is None
    assert validate_behavior_lifecycle(events[:-1]) == "behavior_lifecycle_missing_submit"
    assert validate_behavior_lifecycle([events[0]]) == "behavior_lifecycle_missing_submit"


def test_action_binding_requires_source_press_and_matching_drag_lifecycle():
    challenge_objects = [
        {
            "temporary_object_id": "tmp_object",
            "bbox_x": 0.10,
            "bbox_y": 0.20,
            "bbox_width": 0.20,
            "bbox_height": 0.30,
        }
    ]
    events = [
        {"type": "pointer_down", "object_id": "tmp_object", "x": 0.20, "y": 0.30},
        {"type": "drag_start", "object_id": "tmp_object", "x": 0.20, "y": 0.30},
        {"type": "drop", "object_id": "tmp_object", "x": 0.80, "y": 1.0},
        {"type": "selection_add", "object_id": "tmp_object", "x": None, "y": None},
    ]

    assert validate_behavior_action_binding(events, challenge_objects, {"tmp_object"}) is None

    outside = [{**event} for event in events]
    outside[0]["x"] = 0.60
    assert validate_behavior_action_binding(outside, challenge_objects, {"tmp_object"}) == "behavior_action_start_outside_source"

    missing = events[:-1]
    assert validate_behavior_action_binding(missing, challenge_objects, {"tmp_object"}) == "behavior_action_binding_missing"


def test_trusted_duration_uses_server_recorded_challenge_span():
    events = [
        BehaviorEvent(type="challenge_loaded", timestamp_ms=100),
        BehaviorEvent(type="pointer_down", object_id="tmp_object", x=0.2, y=0.3, timestamp_ms=500),
        BehaviorEvent(type="submit", timestamp_ms=2400),
    ]

    assert trusted_duration_ms(events, 100) == 2300
    assert trusted_duration_ms(events[:-1], 100) == 100


def test_batch_delivery_timing_flags_a_long_trace_uploaded_in_a_short_burst():
    events = [
        BehaviorEvent(type="challenge_loaded", timestamp_ms=100),
        BehaviorEvent(type="submit", timestamp_ms=3_100),
    ]
    received_at = [
        datetime(2026, 7, 27, 1, 0, 0),
        datetime(2026, 7, 27, 1, 0, 0) + timedelta(milliseconds=180),
    ]

    signal = detect_batch_delivery_timing(events, received_at)

    assert signal["detected"] is True
    assert signal["client_event_span_ms"] == 3000
    assert signal["server_batch_span_ms"] == 180


def test_batch_delivery_timing_accepts_progressive_normal_delivery():
    events = [
        BehaviorEvent(type="challenge_loaded", timestamp_ms=100),
        BehaviorEvent(type="submit", timestamp_ms=3_100),
    ]
    received_at = [
        datetime(2026, 7, 27, 1, 0, 0),
        datetime(2026, 7, 27, 1, 0, 0) + timedelta(milliseconds=3_050),
    ]

    signal = detect_batch_delivery_timing(events, received_at)

    assert signal["detected"] is False
    assert signal["delivery_discrepancy_ms"] == 0


def test_verify_rejects_duplicate_selected_objects_before_recording(monkeypatch):
    monkeypatch.setattr(
        main.database,
        "challenge_for_verify",
        lambda _challenge_id: {
            "session_id": "session-123",
            "status": "issued",
            "expires_at": datetime(2030, 1, 1),
            "attempt_count": 0,
            "objects": [],
        },
    )
    payload = main.VerifyRequest(
        selected_object_ids=["tmp_target", "tmp_target"],
        session_id="session-123",
        duration_ms=100,
    )

    with pytest.raises(HTTPException) as exc_info:
        main.verify("challenge-1", payload, None, main.settings.site_key)

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "Duplicate selected object"


def test_stop_go_signal_requires_pause_restart_and_terminal_correction():
    stop_go_events = [
        BehaviorEvent(type="pointer_down", object_id="tmp_object", x=0.10, y=0.10, timestamp_ms=100),
        BehaviorEvent(type="drag_start", object_id="tmp_object", x=0.10, y=0.10, timestamp_ms=100),
        BehaviorEvent(type="pointer_move", object_id="tmp_object", x=0.46, y=0.62, timestamp_ms=260),
        BehaviorEvent(type="pointer_move", object_id="tmp_object", x=0.46, y=0.62, timestamp_ms=670),
        BehaviorEvent(type="pointer_move", object_id="tmp_object", x=0.73, y=0.78, timestamp_ms=930),
        BehaviorEvent(type="pointer_move", object_id="tmp_object", x=0.82, y=0.83, timestamp_ms=1060),
        BehaviorEvent(type="pointer_move", object_id="tmp_object", x=0.80, y=0.80, timestamp_ms=1180),
        BehaviorEvent(type="drop", object_id="tmp_object", x=0.80, y=0.80, timestamp_ms=1190),
    ]

    signal = detect_stop_go_signal(stop_go_events)

    assert signal == {
        "detected": True,
        "pause_restart_count": 1,
        "terminal_correction": True,
        "sparse_pause_restart": True,
    }


def test_stop_go_signal_does_not_flag_a_continuous_drag():
    normal_events = [
        BehaviorEvent(type="pointer_down", object_id="tmp_object", x=0.10, y=0.10, timestamp_ms=100),
        BehaviorEvent(type="drag_start", object_id="tmp_object", x=0.10, y=0.10, timestamp_ms=100),
        BehaviorEvent(type="pointer_move", object_id="tmp_object", x=0.22, y=0.28, timestamp_ms=300),
        BehaviorEvent(type="pointer_move", object_id="tmp_object", x=0.48, y=0.53, timestamp_ms=550),
        BehaviorEvent(type="pointer_move", object_id="tmp_object", x=0.75, y=0.78, timestamp_ms=800),
        BehaviorEvent(type="drop", object_id="tmp_object", x=0.75, y=0.78, timestamp_ms=840),
    ]

    assert detect_stop_go_signal(normal_events)["detected"] is False


def test_stop_go_signal_flags_sparse_pause_restart_without_terminal_correction():
    pause_restart_events = [
        BehaviorEvent(type="pointer_down", object_id="tmp_object", x=0.10, y=0.10, timestamp_ms=100),
        BehaviorEvent(type="drag_start", object_id="tmp_object", x=0.10, y=0.10, timestamp_ms=100),
        BehaviorEvent(type="pointer_move", object_id="tmp_object", x=0.28, y=0.25, timestamp_ms=250),
        BehaviorEvent(type="pointer_move", object_id="tmp_object", x=0.28, y=0.25, timestamp_ms=680),
        BehaviorEvent(type="pointer_move", object_id="tmp_object", x=0.51, y=0.45, timestamp_ms=900),
        BehaviorEvent(type="pointer_move", object_id="tmp_object", x=0.70, y=0.70, timestamp_ms=1140),
        BehaviorEvent(type="pointer_move", object_id="tmp_object", x=0.75, y=0.78, timestamp_ms=1280),
        BehaviorEvent(type="drop", object_id="tmp_object", x=0.75, y=0.78, timestamp_ms=1290),
    ]

    signal = detect_stop_go_signal(pause_restart_events)

    assert signal["detected"] is False
    assert signal["sparse_pause_restart"] is True


def test_payload_hash_survives_a_storage_round_trip_of_high_precision_floats():
    """배치 해시가 저장→재읽기 왕복에서 살아남아야 한다.

    이게 깨지면 verify 가 behavior_batch_payload_invalid 로 배치를 통째로 버리고,
    모델은 호출조차 되지 않는다. 그런데 캡차는 정상 통과하므로(fail-open) 화면에도
    로그에도 아무 증상이 없다 — 수집이 조용히 0 이 된다. 실제로 그랬다.

    17자리 float 이 JSON 컬럼을 거치며 마지막 자리가 흔들리는 것이 원인이었다.
    여기서는 그 흔들림을 1e-12 만큼 직접 주입해 재현한다.
    """
    import json

    from app.db import _canonical_events, _payload_hash

    events = [
        {"seq": 0, "type": "challenge_loaded", "object_id": None,
         "x": None, "y": None, "timestamp_ms": 1_700_000_000_000},
        {"seq": 1, "type": "pointer_down", "object_id": "tmp_a",
         "x": 0.20 + (0.845 - 0.20) * (1 / 7), "y": 0.30000000000000004,
         "timestamp_ms": 1_700_000_000_045},
        {"seq": 2, "type": "drop", "object_id": "tmp_a",
         "x": 0.8449999999999999, "y": 0.7999999999999999,
         "timestamp_ms": 1_700_000_000_090},
    ]

    ingest = _payload_hash(events)

    # 저장 계층이 마지막 자리를 흔든 뒤 되돌려준 상태
    drifted = json.loads(json.dumps(_canonical_events(events)))
    for row in drifted:
        if isinstance(row.get("x"), float):
            row["x"] += 1e-12
            row["y"] -= 1e-12

    assert _payload_hash(drifted) == ingest, "왕복 후 배치 해시가 달라졌다"


def test_canonical_events_does_not_touch_anything_but_coordinates():
    """정규화가 좌표 외 필드를 건드리면 순번·타입 검증이 엉킨다."""
    from app.db import _canonical_events

    events = [{"seq": 3, "type": "pointer_move", "object_id": "tmp_a",
               "x": 0.123456789, "y": None, "timestamp_ms": 1_700_000_000_123}]
    out = _canonical_events(events)

    assert out[0]["seq"] == 3
    assert out[0]["type"] == "pointer_move"
    assert out[0]["object_id"] == "tmp_a"
    assert out[0]["timestamp_ms"] == 1_700_000_000_123
    assert out[0]["y"] is None
    assert out[0]["x"] == pytest.approx(0.123457, abs=1e-9)
    assert events[0]["x"] == 0.123456789, "입력을 제자리에서 바꾸면 안 된다"


def test_predict_payload_always_carries_a_participant_id():
    """참여자 구분이 payload 에 실려야 한다.

    빠져도 아무 데서도 안 터진다 — DB 컬럼이 NULL 을 받고 수집은 계속 돈다.
    그런데 오탐률을 참여자 단위로 못 재게 되고, 수집이 끝난 뒤에는 되붙일 수
    없다. 그래서 조용히 빠지는 것을 여기서 막는다.
    """
    from app.behavior_client import build_predict_payload

    events = [
        {"type": "pointer_down", "object_id": "tmp_a", "x": 0.2, "y": 0.3, "timestamp_ms": 100},
        {"type": "pointer_move", "object_id": "tmp_a", "x": 0.4, "y": 0.5, "timestamp_ms": 240},
        {"type": "drop", "object_id": "tmp_a", "x": 0.8, "y": 0.8, "timestamp_ms": 380},
    ]
    common = dict(attempt_id="a1", challenge_id="c1", events=events,
                  width=500, height=500, retry_count=0)

    payload, reason = build_predict_payload(session_id="session-abcdef12", **common)
    assert reason is None
    assert payload["anonymous_participant_id"] == "session-abcdef12", "세션으로 대체되지 않았다"

    # 명시적으로 주면 그쪽이 이긴다 — 수집 세션에서 참여자 코드를 부여할 때 쓴다.
    payload, _ = build_predict_payload(
        session_id="session-abcdef12", anonymous_participant_id="P07", **common
    )
    assert payload["anonymous_participant_id"] == "P07"


def test_pointer_signal_floats_survive_a_storage_round_trip():
    """PointerEvent 원천 신호의 float 도 왕복에서 살아남아야 한다.

    b04c315 와 똑같은 함정이다. 좌표만 반올림하고 pressure·event_timestamp 를
    빠뜨리면, 저장 계층이 마지막 자리를 흔드는 순간 배치 해시 재검증이 깨진다.
    그러면 verify 가 배치를 통째로 버리고 모델은 호출조차 안 되는데, 캡차는
    fail-open 이라 정상 통과해서 증상이 전혀 안 보인다. 수집만 조용히 0 이 된다.
    """
    import json

    from app.db import _FLOAT_PRECISION, _canonical_events, _payload_hash

    events = [{
        "seq": 0, "type": "pointer_move", "object_id": "tmp_a",
        "x": 0.20 + (0.845 - 0.20) * (1 / 7), "y": 0.30000000000000004,
        "timestamp_ms": 1_700_000_000_000,
        "is_trusted": True, "pointer_type": "mouse",
        "pressure": 0.5000000000000001,
        "pointer_width": 23.456789012345,
        "pointer_height": 1.0000000000000002,
        "buttons": 1, "is_primary": True,
        "event_timestamp": 12345.678901234,
        "coalesced_count": 7,
    }]

    ingest = _payload_hash(events)
    drifted = json.loads(json.dumps(_canonical_events(events)))
    for row in drifted:
        for field in _FLOAT_PRECISION:
            if isinstance(row.get(field), float):
                row[field] += 1e-12

    assert _payload_hash(drifted) == ingest, "왕복 후 배치 해시가 달라졌다"


def test_every_float_wire_field_is_canonicalised():
    """새 float 필드를 스키마에만 추가하고 정규화에서 빠뜨리는 것을 막는다.

    이게 이 버그의 실제 발생 경로다 — 필드를 늘릴 때 스키마는 고치고 해시 쪽은
    잊는다. 모델 정의에서 float 필드를 뽑아 대조한다.
    """
    from app.db import _FLOAT_PRECISION
    from app.main import BehaviorBatchEvent

    float_fields = {
        name for name, info in BehaviorBatchEvent.model_fields.items()
        if "float" in str(info.annotation)
    }
    missing = float_fields - set(_FLOAT_PRECISION)
    assert not missing, f"정규화에서 빠진 float 필드: {missing}"


def test_null_pointer_signals_do_not_break_canonicalisation():
    """미지원 브라우저는 전부 null 을 보낸다. 그래도 해시가 안정적이어야 한다."""
    from app.db import _payload_hash

    events = [{
        "seq": 0, "type": "pointer_move", "object_id": "tmp_a",
        "x": 0.25, "y": 0.5, "timestamp_ms": 1_700_000_000_000,
        "is_trusted": None, "pointer_type": None, "pressure": None,
        "pointer_width": None, "pointer_height": None, "buttons": None,
        "is_primary": None, "event_timestamp": None, "coalesced_count": None,
    }]
    assert _payload_hash(events) == _payload_hash([dict(events[0])])
