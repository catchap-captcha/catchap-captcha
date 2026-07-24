from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.db import _payload_hash, _receipt_hash, _receipt_timestamp, _validate_behavior_batches
from app.main import BehaviorBatchRequest


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
