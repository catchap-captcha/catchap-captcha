from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.error import URLError

from app.behavior_client import (
    BehaviorAIClient,
    BehaviorPrediction,
    adapt_events,
    build_predict_payload,
    resolve_final_verdict,
)


def _events():
    return [
        {"type": "challenge_loaded", "timestamp_ms": 100, "x": None, "y": None},
        {"type": "pointer_down", "object_id": "tmp_a", "timestamp_ms": 500, "x": 0.1, "y": 0.2},
        {"type": "drag_start", "object_id": "tmp_a", "timestamp_ms": 500, "x": 0.1, "y": 0.2},
        {"type": "pointer_move", "object_id": "tmp_a", "timestamp_ms": 560, "x": 0.4, "y": 0.5},
        {"type": "drop", "object_id": "tmp_a", "timestamp_ms": 640, "x": 0.7, "y": 0.8},
        {"type": "selection_add", "object_id": "tmp_a", "timestamp_ms": 641, "x": 0.7, "y": 0.8},
    ]


def test_adapter_maps_pointer_events_without_duplicate_drag_start():
    events, counters = adapt_events(_events(), width=1000, height=500)

    assert [event["event_type"] for event in events] == ["pointerdown", "pointermove", "pointerup"]
    assert events[1]["x"] == 400.0
    assert events[1]["y"] == 250.0
    assert counters == {
        "drag_start_count": 1,
        "drop_count": 1,
        "selection_count": 1,
        "pointercancel_count": 0,
    }


def test_predict_payload_excludes_answer_semantics_and_tracks_interaction():
    presented_at = datetime(2026, 7, 23, 7, 0, tzinfo=timezone.utc)
    submitted_at = datetime(2026, 7, 23, 7, 0, 5, tzinfo=timezone.utc)
    payload, reason = build_predict_payload(
        attempt_id="ms-challenge-a1",
        challenge_id="challenge",
        session_id="s" * 128,
        events=_events(),
        width=800,
        height=600,
        retry_count=2,
        presented_at=presented_at,
        submitted_at=submitted_at,
    )

    assert reason is None
    assert payload is not None
    assert len(payload["session_id"]) == 64
    assert payload["interaction"] == {
        "regrab_count": 0,
        "retry_count": 2,
        "pointercancel_count": 0,
        "empty_click_count": 0,
        "failed_drop_count": 0,
    }
    assert payload["timing"] == {
        "presented_at": "2026-07-23T07:00:00+00:00",
        "submitted_at": "2026-07-23T07:00:05+00:00",
    }
    assert "selected_object_ids" not in json.dumps(payload)


def test_shadow_mode_never_changes_a_correct_captcha_verdict():
    prediction = BehaviorPrediction(
        attempt_id="attempt",
        status="scored",
        risk_score=99.0,
        risk_level="high",
        recommended_action="step_up_and_rate_limit",
        policy_mode="active",
    )

    verdict, action = resolve_final_verdict(
        captcha_correct=True,
        prediction=prediction,
        local_policy_mode="shadow",
    )

    assert verdict == "passed"
    assert action is None


def test_active_mode_requires_both_services_to_enable_enforcement():
    prediction = BehaviorPrediction(
        attempt_id="attempt",
        status="scored",
        risk_score=99.0,
        risk_level="high",
        recommended_action="step_up_and_rate_limit",
        policy_mode="active",
    )

    verdict, action = resolve_final_verdict(
        captcha_correct=True,
        prediction=prediction,
        local_policy_mode="active",
    )

    assert verdict == "failed"
    assert action == "step_up_and_rate_limit"


class _Response:
    def __init__(self, body: dict):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.body).encode()


def test_client_uses_private_backend_header_and_parses_prediction():
    calls = []

    def opener(request, timeout):
        calls.append((request.full_url, request.get_header("X-captcha-backend-key"), timeout))
        return _Response(
            {
                "risk_score": 42.0,
                "risk_level": "medium",
                "recommended_action": "step_up",
                "policy_mode": "shadow",
                "human_score": 0.6,
                "bot_risk_score": 0.4,
                "model_name": "lightgbm",
                "model_version": "candidate",
                "feature_schema_version": "2.3",
                "reasons": ["model_score"],
            }
        )

    client = BehaviorAIClient("http://behavior.internal", "private-key", 1.5, opener=opener)
    prediction = client.score({"events": [{}]}, "attempt", None)

    assert prediction.status == "scored"
    assert prediction.recommended_action == "step_up"
    assert calls == [("http://behavior.internal/api/v1/behavior/predict", "private-key", 1.5)]


def test_client_keeps_the_full_two_view_policy_response():
    client = BehaviorAIClient(
        "http://behavior.internal",
        "private-key",
        1.5,
        opener=lambda *_args, **_kwargs: _Response(
            {
                "risk_score": 87.0,
                "risk_level": "high",
                "recommended_action": "step_up_and_rate_limit",
                "policy_mode": "shadow",
                "human_score": 0.6,
                "bot_risk_score": 0.4,
                "model_name": "lightgbm_general_dynamics_min_fusion",
                "model_version": "revalidation_two_view_participant_safe_20260722",
                "feature_schema_version": "2.3",
                "reasons": ["two_view_fusion", "replay_signal"],
            }
        ),
    )

    prediction = client.score({"events": [{}]}, "attempt", None)

    assert prediction.status == "scored"
    assert prediction.risk_score == 87.0
    assert prediction.recommended_action == "step_up_and_rate_limit"
    assert prediction.policy_mode == "shadow"
    assert prediction.model_version == "revalidation_two_view_participant_safe_20260722"
    assert prediction.reasons == ("two_view_fusion", "replay_signal")


def test_client_converts_a_candidate_model_score_to_shadow_only():
    client = BehaviorAIClient(
        "http://behavior.internal",
        "private-key",
        1.5,
        opener=lambda *_args, **_kwargs: _Response(
            {
                "human_score": 0.72,
                "bot_risk_score": 0.28,
                "model_name": "random_forest",
                "model_version": "candidate-local",
                "feature_schema_version": "1.0",
            }
        ),
    )

    prediction = client.score({"events": [{}]}, "attempt", None)

    assert prediction.status == "scored"
    assert prediction.risk_score == 28.0
    assert prediction.recommended_action == "allow"
    assert prediction.policy_mode == "shadow"


def test_invalid_behavior_response_fails_open_for_the_captcha_server():
    client = BehaviorAIClient(
        "http://behavior.internal",
        "private-key",
        1.5,
        opener=lambda *_args, **_kwargs: _Response({"risk_score": 10}),
    )

    prediction = client.score({"events": [{}]}, "attempt", None)

    assert prediction.status == "error"
    assert prediction.detail == "behavior_ai_invalid_action"


def test_readiness_requires_a_reachable_service_with_a_loaded_model():
    calls = []

    def opener(request, timeout):
        calls.append((request.full_url, request.get_method(), request.get_header("X-captcha-backend-key"), timeout))
        return _Response(
            {
                "status": "ok",
                "model_loaded": True,
                "model_name": "lightgbm_general_dynamics_min_fusion",
                "model_version": "revalidation_two_view_participant_safe_20260722",
                "feature_schema_version": "2.3",
                "policy_mode": "shadow",
            }
        )

    readiness = BehaviorAIClient("http://behavior.internal", "private-key", 1.5, opener=opener).readiness()

    assert readiness.ready is True
    assert readiness.model_version == "revalidation_two_view_participant_safe_20260722"
    assert calls == [("http://behavior.internal/health", "GET", "private-key", 1.5)]


def test_readiness_rejects_a_degraded_or_unreachable_behavior_service():
    degraded = BehaviorAIClient(
        "http://behavior.internal",
        "private-key",
        1.5,
        opener=lambda *_args, **_kwargs: _Response({"status": "degraded", "model_loaded": True}),
    ).readiness()
    unreachable = BehaviorAIClient(
        "http://behavior.internal",
        "private-key",
        1.5,
        opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(URLError("offline")),
    ).readiness()

    assert degraded.ready is False
    assert degraded.detail == "behavior_ai_degraded"
    assert unreachable.ready is False
    assert unreachable.detail == "behavior_ai_request_failed:URLError"
