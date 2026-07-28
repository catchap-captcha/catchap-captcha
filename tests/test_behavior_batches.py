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
