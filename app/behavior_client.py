"""Server-side bridge from the object-drag CAPTCHA to the behavior AI.

The browser sends events only to the CAPTCHA API. This module converts that
trusted payload to the behavior-service contract and calls the service with the
private backend key. In shadow mode, unavailable scoring can never change the
CAPTCHA verdict.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable, Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


Action = Literal["allow", "step_up", "step_up_and_rate_limit"]
PolicyMode = Literal["shadow", "active"]

_EVENT_TYPE_MAP = {
    "pointer_down": "pointerdown",
    "drag_start": "pointerdown",
    "pointer_move": "pointermove",
    "drop": "pointerup",
    "pointer_cancel": "pointercancel",
}


@dataclass(frozen=True)
class BehaviorPrediction:
    attempt_id: str
    status: Literal["scored", "disabled", "unavailable", "error"]
    detail: str | None = None
    risk_score: float | None = None
    risk_level: str | None = None
    recommended_action: Action | None = None
    policy_mode: PolicyMode | None = None
    human_score: float | None = None
    bot_risk_score: float | None = None
    model_name: str | None = None
    model_version: str | None = None
    feature_schema_version: str | None = None
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class BehaviorAIReadiness:
    """Deployment-safe health summary for the internal behavior service."""

    configured: bool
    reachable: bool
    model_loaded: bool
    service_status: str | None = None
    model_name: str | None = None
    model_version: str | None = None
    feature_schema_version: str | None = None
    policy_mode: str | None = None
    detail: str | None = None

    @property
    def ready(self) -> bool:
        return self.configured and self.reachable and self.model_loaded

    def as_dict(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "reachable": self.reachable,
            "model_loaded": self.model_loaded,
            "ready": self.ready,
            "service_status": self.service_status,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "feature_schema_version": self.feature_schema_version,
            "policy_mode": self.policy_mode,
            "detail": self.detail,
        }


def behavior_attempt_id(challenge_id: str, attempt_number: int) -> str:
    """Stable, bounded ID shared with the behavior service for idempotency."""
    return f"ms-{challenge_id}-a{attempt_number}"[:64]


def _bounded_session_id(session_id: str) -> str:
    if len(session_id) <= 64:
        return session_id
    return "ms-" + hashlib.sha256(session_id.encode()).hexdigest()[:61]


def _event_value(event: Any, name: str, default: Any = None) -> Any:
    if isinstance(event, dict):
        return event.get(name, default)
    return getattr(event, name, default)


def adapt_events(events: Iterable[Any], width: int, height: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Map ms drag events to the existing behavior-model pointer schema.

    ``drag_start`` follows ``pointer_down`` in the current UI. Keeping both
    would create an artificial duplicate point, so the duplicate is omitted.
    """
    output: list[dict[str, Any]] = []
    counters = {
        "drag_start_count": 0,
        "drop_count": 0,
        "selection_count": 0,
        "pointercancel_count": 0,
    }
    last_down: tuple[str | None, int | None] | None = None

    for event in events:
        source_type = _event_value(event, "type")
        object_id = _event_value(event, "object_id")
        timestamp_ms = _event_value(event, "timestamp_ms")

        if source_type == "drag_start":
            counters["drag_start_count"] += 1
        elif source_type == "drop":
            counters["drop_count"] += 1
        elif source_type == "selection_add":
            counters["selection_count"] += 1
        elif source_type == "pointer_cancel":
            counters["pointercancel_count"] += 1

        mapped_type = _EVENT_TYPE_MAP.get(source_type)
        if mapped_type is None:
            continue
        x = _event_value(event, "x")
        y = _event_value(event, "y")
        if x is None or y is None or timestamp_ms is None:
            continue

        if source_type == "drag_start" and last_down == (object_id, timestamp_ms):
            continue
        if source_type == "pointer_down":
            last_down = (object_id, timestamp_ms)

        x_normalized = max(0.0, min(1.0, float(x)))
        y_normalized = max(0.0, min(1.0, float(y)))
        output.append(
            {
                "seq": len(output),
                "event_type": mapped_type,
                "t_ms": int(timestamp_ms),
                "x": x_normalized * width,
                "y": y_normalized * height,
                "x_normalized": x_normalized,
                "y_normalized": y_normalized,
                "target_role": None,
            }
        )

    return output, counters


def build_predict_payload(
    *,
    attempt_id: str,
    challenge_id: str,
    session_id: str,
    events: Iterable[Any],
    width: int,
    height: int,
    retry_count: int,
    presented_at: datetime | None = None,
    submitted_at: datetime | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Return a behavior request without answer semantics or object IDs.

    Pointer events originate in the browser and remain untrusted. The optional
    timing values are supplied by the CAPTCHA server so the behavior service
    can reject telemetry that falls outside the challenge lifecycle.
    """
    pointer_events, counters = adapt_events(events, width, height)
    if len(pointer_events) < 2:
        return None, "insufficient_pointer_events"

    return (
        {
            "schema_version": "1.0",
            "attempt_id": attempt_id,
            "challenge_id": challenge_id,
            "session_id": _bounded_session_id(session_id),
            "captcha": {"width": width, "height": height},
            "timing": {
                "presented_at": presented_at.isoformat() if presented_at else None,
                "submitted_at": submitted_at.isoformat() if submitted_at else None,
            },
            "events": pointer_events,
            "interaction": {
                "regrab_count": max(0, counters["drag_start_count"] - counters["selection_count"]),
                "retry_count": max(0, retry_count),
                "pointercancel_count": counters["pointercancel_count"],
                "empty_click_count": 0,
                "failed_drop_count": max(0, counters["drop_count"] - counters["selection_count"]),
            },
        },
        None,
    )


class BehaviorAIClient:
    """Small synchronous HTTP client used only by the trusted CAPTCHA server."""

    def __init__(
        self,
        base_url: str,
        backend_key: str,
        timeout_seconds: float,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.backend_key = backend_key
        self.timeout_seconds = timeout_seconds
        self._opener = opener

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.backend_key)

    def readiness(self) -> BehaviorAIReadiness:
        """Check the internal scorer without exposing its URL or backend key.

        A configured but degraded service is deliberately not considered ready:
        scoring would otherwise appear healthy while the service cannot persist
        shadow outcomes or has not loaded its promoted model bundle.
        """
        if not self.enabled:
            return BehaviorAIReadiness(False, False, False, detail="behavior_ai_not_configured")

        try:
            body = self._get("/health")
        except HTTPError as error:
            return BehaviorAIReadiness(
                True,
                False,
                False,
                detail=f"behavior_ai_http_{error.code}",
            )
        except (OSError, URLError, ValueError) as error:
            return BehaviorAIReadiness(
                True,
                False,
                False,
                detail=f"behavior_ai_request_failed:{type(error).__name__}",
            )

        if not isinstance(body, dict):
            return BehaviorAIReadiness(True, False, False, detail="behavior_ai_invalid_health_response")

        service_status = body.get("status")
        model_loaded = body.get("model_loaded")
        if service_status not in {"ok", "degraded"} or not isinstance(model_loaded, bool):
            return BehaviorAIReadiness(True, False, False, detail="behavior_ai_invalid_health_response")

        return BehaviorAIReadiness(
            configured=True,
            reachable=service_status == "ok",
            model_loaded=model_loaded,
            service_status=service_status,
            model_name=_optional_string(body.get("model_name")),
            model_version=_optional_string(body.get("model_version")),
            feature_schema_version=_optional_string(body.get("feature_schema_version")),
            policy_mode=_optional_string(body.get("policy_mode")),
            detail=None if service_status == "ok" else "behavior_ai_degraded",
        )

    def score(self, payload: dict[str, Any] | None, attempt_id: str, reason: str | None) -> BehaviorPrediction:
        if not self.enabled:
            return BehaviorPrediction(attempt_id, "disabled", "behavior_ai_not_configured")
        if payload is None:
            return BehaviorPrediction(attempt_id, "unavailable", reason or "invalid_behavior_payload")

        try:
            body = self._post("/api/v1/behavior/predict", payload)
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:300]
            state = "unavailable" if error.code == 503 else "error"
            return BehaviorPrediction(attempt_id, state, f"behavior_ai_http_{error.code}:{detail}")
        except (OSError, URLError, ValueError) as error:
            return BehaviorPrediction(attempt_id, "error", f"behavior_ai_request_failed:{type(error).__name__}")

        # The behavior API is being rolled out in stages. Its older candidate
        # endpoint returns core model fields only, so turn that valid local
        # score into a shadow-only recommendation instead of failing closed.
        # A full policy response also contains those core fields, therefore it
        # must be distinguished before falling back to the legacy adapter.
        if self._is_candidate_score_response(body):
            return self._candidate_score_prediction(body, attempt_id)

        try:
            action = body.get("recommended_action")
            policy_mode = body.get("policy_mode")
            if action not in {"allow", "step_up", "step_up_and_rate_limit"}:
                return BehaviorPrediction(attempt_id, "error", "behavior_ai_invalid_action")
            if policy_mode not in {"shadow", "active"}:
                return BehaviorPrediction(attempt_id, "error", "behavior_ai_invalid_policy_mode")

            return BehaviorPrediction(
                attempt_id=attempt_id,
                status="scored",
                risk_score=float(body["risk_score"]),
                risk_level=str(body["risk_level"]),
                recommended_action=action,
                policy_mode=policy_mode,
                human_score=float(body["human_score"]),
                bot_risk_score=float(body["bot_risk_score"]),
                model_name=str(body["model_name"]),
                model_version=str(body["model_version"]),
                feature_schema_version=str(body["feature_schema_version"]),
                reasons=tuple(str(reason) for reason in body.get("reasons", [])),
            )
        except (AttributeError, KeyError, TypeError, ValueError):
            return BehaviorPrediction(attempt_id, "error", "behavior_ai_invalid_response")

    @staticmethod
    def _is_candidate_score_response(body: Any) -> bool:
        if not isinstance(body, dict):
            return False
        legacy_fields = {"human_score", "bot_risk_score", "model_name", "model_version"}
        full_policy_fields = {"risk_score", "risk_level", "recommended_action", "policy_mode"}
        return legacy_fields.issubset(body) and not full_policy_fields.issubset(body)

    @staticmethod
    def _candidate_score_prediction(body: dict[str, Any], attempt_id: str) -> BehaviorPrediction:
        bot_risk = max(0.0, min(1.0, float(body["bot_risk_score"])))
        risk_score = round(bot_risk * 100.0, 2)
        if risk_score < 30:
            risk_level, action = "low", "allow"
        elif risk_score < 60:
            risk_level, action = "medium", "step_up"
        else:
            risk_level, action = "high", "step_up_and_rate_limit"
        return BehaviorPrediction(
            attempt_id=attempt_id,
            status="scored",
            risk_score=risk_score,
            risk_level=risk_level,
            recommended_action=action,
            # Candidate models never block traffic. The CAPTCHA policy remains
            # shadow until a model has passed the promotion criteria.
            policy_mode="shadow",
            human_score=float(body["human_score"]),
            bot_risk_score=bot_risk,
            model_name=str(body["model_name"]),
            model_version=str(body["model_version"]),
            feature_schema_version=str(body.get("feature_schema_version", "1.0")),
            reasons=("candidate_model_score",),
        )

    def record_shadow_outcome(self, prediction: BehaviorPrediction, verdict: Literal["passed", "failed"]) -> BehaviorPrediction:
        if prediction.status != "scored" or prediction.policy_mode != "shadow":
            return prediction
        try:
            self._post(
                "/api/v1/behavior/shadow/outcomes",
                {
                    "attempt_id": prediction.attempt_id,
                    "main_captcha_verdict": verdict,
                    "final_verdict": verdict,
                },
            )
        except HTTPError as error:
            # Older local candidate servers do not persist shadow outcomes.
            # Their score is still valid for this CAPTCHA-side local test.
            if error.code == 404:
                return prediction
            return BehaviorPrediction(
                **{**prediction.__dict__, "detail": "shadow_outcome_failed:HTTPError"}
            )
        except (OSError, URLError, ValueError) as error:
            return BehaviorPrediction(
                **{**prediction.__dict__, "detail": f"shadow_outcome_failed:{type(error).__name__}"}
            )
        return prediction

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            self.base_url + path,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Captcha-Backend-Key": self.backend_key,
            },
            method="POST",
        )
        with self._opener(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    def _get(self, path: str) -> Any:
        request = Request(
            self.base_url + path,
            headers={"X-Captcha-Backend-Key": self.backend_key},
            method="GET",
        )
        with self._opener(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))


def _optional_string(value: Any) -> str | None:
    return str(value) if isinstance(value, str) else None


def resolve_final_verdict(
    *,
    captcha_correct: bool,
    prediction: BehaviorPrediction,
    local_policy_mode: PolicyMode,
) -> tuple[Literal["passed", "failed"], str | None]:
    """Decide final verdict only when both services explicitly enable active mode."""
    if not captcha_correct:
        return "failed", None
    if (
        local_policy_mode == "active"
        and prediction.status == "scored"
        and prediction.policy_mode == "active"
        and prediction.recommended_action in {"step_up", "step_up_and_rate_limit"}
    ):
        return "failed", prediction.recommended_action
    return "passed", None
