from __future__ import annotations

import hashlib
import io
import hmac
import json
import math
import os
import secrets
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field, field_validator

from .behavior_client import (
    BehaviorAIClient,
    BehaviorPrediction,
    behavior_attempt_id,
    build_predict_payload,
    resolve_final_verdict,
)
from . import asset_storage as asset_store
from .config import settings
from .db import Database, utcnow


database = Database(settings)
settings.validate()
# 행동 AI 브릿지. URL/키가 비면 enabled=False → 캡챠 판정에 영향 없음.
behavior_ai = BehaviorAIClient(
    settings.behavior_ai_url,
    settings.behavior_ai_backend_key,
    settings.behavior_ai_timeout_seconds,
)


class ChallengeCreate(BaseModel):
    purpose: Literal["signup", "login", "recovery", "lecture"] = "signup"
    risk_level: Literal["low", "medium", "high"] = "medium"
    session_id: str = Field(min_length=8, max_length=128)
    lecture_id: str | None = Field(default=None, max_length=128)
    playback_position: float | None = Field(default=None, ge=0)


class BehaviorEvent(BaseModel):
    type: Literal["challenge_loaded", "object_enter", "object_leave", "pointer_down", "drag_start",
                  "pointer_move", "pointer_cancel", "drop", "selection_add", "object_removed", "submit", "verify_result"]
    object_id: str | None = Field(default=None, max_length=64)
    x: float | None = Field(default=None, ge=0, le=1)
    y: float | None = Field(default=None, ge=0, le=1)
    timestamp_ms: int = Field(ge=0)
    # sw 요청: PointerEvent 원천 신호(궤적 밖 특징). 전부 Optional·느슨 검증 → 미지원 시 null이지 거부 아님.
    # isTrusted=JS 합성이벤트 판별, coalesced_count=실브라우저 없이 못 만드는 값, pressure/geometry=입력장치 특성.
    is_trusted: bool | None = None
    pointer_type: str | None = Field(default=None, max_length=16)
    pressure: float | None = None
    pointer_width: float | None = None
    pointer_height: float | None = None
    buttons: int | None = None
    is_primary: bool | None = None
    event_timestamp: float | None = None
    coalesced_count: int | None = None


class BehaviorBatchEvent(BehaviorEvent):
    seq: int = Field(ge=0)


class BehaviorBatchRequest(BaseModel):
    session_id: str = Field(min_length=8, max_length=128)
    nonce: str = Field(min_length=24, max_length=128)
    batch_seq: int = Field(ge=0, le=64)
    previous_receipt: str | None = Field(default=None, min_length=64, max_length=64)
    events: list[BehaviorBatchEvent] = Field(min_length=1, max_length=32)


class VerifyRequest(BaseModel):
    selected_object_ids: list[str] = Field(max_length=12)
    session_id: str = Field(min_length=8, max_length=128)
    duration_ms: int = Field(ge=100, le=180000)
    events: list[BehaviorEvent] = Field(default_factory=list, max_length=600)
    client_signals: dict | None = Field(default=None)
    pow_nonce: str | None = Field(default=None, max_length=64)
    # 수집 세션용 참여자 코드(?participant=). 없으면 서버가 session_id 로 폴백한다.
    participant_id: str | None = Field(default=None, max_length=64)


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=256)
    captcha_token: str = Field(min_length=32, max_length=256)
    session_id: str = Field(min_length=8, max_length=128)


class VerifyTokenRequest(BaseModel):
    token: str = Field(min_length=32, max_length=256)
    session_id: str = Field(min_length=8, max_length=128)
    lecture_id: str | None = Field(default=None, max_length=128)
    purpose: Literal["signup", "login", "recovery", "lecture"] = "lecture"


class ReviewObject(BaseModel):
    object_key: str
    label: str = "giraffe"
    x: float
    y: float
    width: float
    height: float
    role: Literal["target", "decoy", "ambiguous", "invalid"]

    @field_validator("x", "y", "width", "height")
    @classmethod
    def _clamp_unit(cls, v: float, info) -> float:
        # 자동 라벨링 데이터의 미세한 범위 밖 값(예: -0.003)을 거부하지 않고 [0,1]로 클램프한다.
        v = min(max(float(v), 0.0), 1.0)
        if info.field_name in ("width", "height") and v <= 0:
            v = 1e-4
        return v


class ReviewRequest(BaseModel):
    queue_id: str
    reviewer: str = Field(min_length=2, max_length=128)
    review_status: Literal["labeled", "approved", "rejected", "needs_revision"]
    instruction_ko: str = Field(min_length=5, max_length=500)
    difficulty: int = Field(ge=1, le=5)
    expected_target_count: int | None = Field(default=None, ge=0, le=50)
    objects: list[ReviewObject] = Field(min_length=1, max_length=20)


def hash_value(value: str) -> str:
    return hmac.new(settings.app_secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def require_header(actual: str | None, expected: str, message: str) -> None:
    if not actual or not hmac.compare_digest(actual, expected):
        raise HTTPException(status_code=401, detail=message)


def require_admin(admin_key: str | None) -> str:
    """관리자 키를 검증하고 해당 검수자 이름을 반환한다. 유효하지 않으면 401."""
    reviewer = settings.reviewer_for_key(admin_key)
    if not reviewer:
        raise HTTPException(status_code=401, detail="Invalid admin key")
    return reviewer


def _leading_zero_bits(digest: bytes) -> int:
    """바이트열의 선행 0비트 수."""
    n = 0
    for b in digest:
        if b == 0:
            n += 8; continue
        n += 8 - b.bit_length(); break
    return n


def pow_verify(seed: str, nonce: str | None, bits: int) -> bool:
    """Proof-of-Work 검증: sha256(seed:nonce)의 선행 0비트가 요구치 이상이어야 통과.
    봇이 챌린지마다 연산비용을 치르게 해 대량공격의 경제성을 깨뜨린다. 서버검증은 sha256 1회로 저렴."""
    if not nonce or not isinstance(nonce, str):
        return False
    digest = hashlib.sha256(f"{seed}:{nonce}".encode()).digest()
    return _leading_zero_bits(digest) >= bits


def _place_honeypot(boxes: list[tuple], rng, size_min: float = 0.06, size_max: float = 0.10, margin: float = 0.03, tries: int = 24):
    """기존 객체와 겹치지 않는 빈 영역 좌표를 찾는다. 못 찾으면 None(그 챌린지는 허니팟 없음)."""
    for _ in range(tries):
        w = rng.uniform(size_min, size_max); h = rng.uniform(size_min, size_max)
        x = rng.uniform(0.0, 1.0 - w); y = rng.uniform(0.0, 1.0 - h)
        ok = True
        for bx, by, bw, bh in boxes:
            if not (x + w + margin < bx or x > bx + bw + margin or y + h + margin < by or y > by + bh + margin):
                ok = False; break
        if ok:
            return (round(x, 4), round(y, 4), round(w, 4), round(h, 4))
    return None


def automation_score(sig: dict | None) -> int:
    """자동화 브라우저 신호 점수. webdriver/헤드리스는 순정 자동화의 강한 흔적."""
    if not isinstance(sig, dict): return 0
    s = 0
    if sig.get("webdriver"): s += 80
    if sig.get("headlessUA"): s += 80
    if sig.get("languages", 1) == 0: s += 15
    if sig.get("cores", 1) == 0: s += 10
    return s


def check_origin(request: Request) -> None:
    """허용 도메인이 설정된 실서비스에서, 브라우저가 아닌 직접 API 호출(우회 봇)을 차단한다."""
    allowed = settings.allowed_origins
    if not allowed or "*" in allowed:
        return  # 개발/미설정(ALLOWED_ORIGINS=*) 시 통과
    origin = request.headers.get("origin") or request.headers.get("referer", "")
    if not origin or not any(origin.startswith(a) for a in allowed):
        raise HTTPException(status_code=403, detail="Origin not allowed")


def client_ip(request: Request) -> str:
    if settings.trust_proxy and request.headers.get("x-forwarded-for"):
        return request.headers["x-forwarded-for"].split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


MANIFEST_KEY = "challenges.jsonl"


def safe_asset(root: Path, relative: str) -> Path:
    """★로컬 파일 전용 안전 검사. 자산은 asset_storage 를 거치므로 여기서 쓰지 않는다.

    labeling_dir 처럼 **로컬에만 있는** 작업용 폴더를 다룰 때를 위해 남겨 둔다."""
    candidate = (root / relative).resolve()
    if root.resolve() not in candidate.parents or not candidate.is_file():
        raise HTTPException(404, "Asset not found")
    return candidate


def summarize(events: list[BehaviorEvent], selected: set[str], targets: set[str], duration_ms: int,
              correct: bool, request_pattern: dict[str, int], ip_changed: bool) -> dict:
    segments: list[list[BehaviorEvent]]=[]; current: list[BehaviorEvent]=[]
    for event in events:
        if event.type=="drag_start" and event.x is not None and event.y is not None:
            if current: segments.append(current)
            current=[event]
        elif current and event.type in {"pointer_move","drop"} and event.x is not None and event.y is not None:
            current.append(event)
            if event.type=="drop": segments.append(current);current=[]
    if current: segments.append(current)
    points=[point for segment in segments for point in segment]
    distances: list[float] = []
    speeds: list[float] = []
    intervals: list[float] = []
    turns = 0.0
    pause_count=0
    for segment in segments:
        for a,b in zip(segment,segment[1:]):
            distance=math.hypot((b.x or 0)-(a.x or 0),(b.y or 0)-(a.y or 0));dt=max(1,b.timestamp_ms-a.timestamp_ms)
            distances.append(distance);speeds.append(distance/dt);intervals.append(dt);pause_count+=dt>450
        for a,b,c in zip(segment,segment[1:],segment[2:]):
            ab=math.atan2((b.y or 0)-(a.y or 0),(b.x or 0)-(a.x or 0));bc=math.atan2((c.y or 0)-(b.y or 0),(c.x or 0)-(b.x or 0))
            turns+=abs(math.atan2(math.sin(bc-ab),math.cos(bc-ab)))
    average = sum(speeds) / len(speeds) if speeds else 0.0
    variance = sum((x-average) ** 2 for x in speeds) / len(speeds) if speeds else 0.0
    loaded = next((e.timestamp_ms for e in events if e.type == "challenge_loaded"), None)
    down = next((e.timestamp_ms for e in events if e.type == "pointer_down"), None)
    dwell_started: dict[str,int] = {}; dwell_ms: dict[str,int] = {}
    for event in events:
        if not event.object_id: continue
        if event.type == "object_enter": dwell_started[event.object_id]=event.timestamp_ms
        elif event.type == "object_leave" and event.object_id in dwell_started:
            dwell_ms[event.object_id]=dwell_ms.get(event.object_id,0)+max(0,event.timestamp_ms-dwell_started.pop(event.object_id))
    end_time=max((event.timestamp_ms for event in events),default=loaded or 0)
    for object_id,start in dwell_started.items(): dwell_ms[object_id]=dwell_ms.get(object_id,0)+max(0,end_time-start)
    selection_order=[event.object_id for event in events if event.type=="selection_add" and event.object_id]
    removal_order=[event.object_id for event in events if event.type=="object_removed" and event.object_id]
    reaction=max(0,down-loaded) if down is not None and loaded is not None else None
    speed_cv=(math.sqrt(variance)/average) if average else 0.0
    dt_mean=sum(intervals)/len(intervals) if intervals else 0.0
    dt_var=sum((d-dt_mean)**2 for d in intervals)/len(intervals) if intervals else 0.0
    dt_cv=(math.sqrt(dt_var)/dt_mean) if dt_mean else 1.0
    max_jump=max(distances,default=0.0)
    components={"answer_accuracy":0,"drag_behavior":0,"reaction_exploration":0,
                "selection_correction":0,"session_behavior":0,"api_pattern":0}
    if not correct: components["answer_accuracy"]=30
    move_count=sum(event.type=="pointer_move" for event in events)
    if move_count<3: components["drag_behavior"]+=20
    if move_count>=3:
        if turns<0.04: components["drag_behavior"]+=8            # 곡률 없는 직선 경로
        if speed_cv<0.04: components["drag_behavior"]+=8         # 등속 이동
        if dt_cv<0.12: components["drag_behavior"]+=10           # 규칙적 타이밍(봇 고유 신호)
        if turns<0.06 and speed_cv<0.06 and dt_cv<0.18: components["drag_behavior"]+=15  # 직선+등속+규칙 복합=확정 봇
    if max_jump>.45: components["drag_behavior"]+=10             # 순간이동
    components["drag_behavior"]=min(45,components["drag_behavior"])
    if reaction is None: components["reaction_exploration"]=12
    elif reaction<300: components["reaction_exploration"]=15
    elif reaction<600: components["reaction_exploration"]=10
    elif reaction<1000: components["reaction_exploration"]=5
    if len(selected-targets): components["selection_correction"]+=7
    if not removal_order and reaction is not None and reaction<600: components["selection_correction"]+=3
    components["selection_correction"]=min(10,components["selection_correction"])
    if request_pattern["session_challenges_10m"]>=8: components["session_behavior"]=10
    elif request_pattern["session_challenges_10m"]>=4: components["session_behavior"]=5
    if request_pattern["session_failures_10m"]>=3: components["session_behavior"]=10
    if request_pattern["ip_challenges_1m"]>=10: components["api_pattern"]=10
    elif request_pattern["ip_challenges_1m"]>=5: components["api_pattern"]=5
    if ip_changed: components["api_pattern"]=min(10,components["api_pattern"]+5)
    risk_score=sum(components.values())
    risk_level="normal" if risk_score<30 else "suspicious" if risk_score<60 else "high" if risk_score<80 else "automated"
    # 행동 지문: 마우스 동역학을 버킷팅해 해시. 같은 풀이툴은 같은 지문을 반복 생성한다.
    sig_parts=(reaction//250 if reaction is not None else -1, round(turns/0.4), round(speed_cv/0.06),
               round(dt_cv/0.06), min(move_count,25), pause_count, round(sum(distances)/0.3))
    behavior_signature=hashlib.sha256(repr(sig_parts).encode()).hexdigest()[:16]
    return {
        "behavior_signature": behavior_signature,
        "reaction_time_ms": reaction,
        "drag_count": sum(e.type == "drag_start" for e in events),
        "wrong_object_count": len(selected-targets), "average_speed": average,
        "speed_variance": variance, "speed_cv":speed_cv,"path_length": sum(distances), "path_curvature": turns,
        "max_pointer_jump":max_jump,"pointer_move_count":move_count,
        "pause_count":pause_count,
        "total_duration_ms": duration_ms,"object_dwell_ms":dwell_ms,"selection_order":selection_order,
        "removal_order":removal_order,"correction_count":len(removal_order),"answer_correct":correct,
        "request_pattern":request_pattern,"ip_changed":ip_changed,"risk_components":components,
        "risk_score":risk_score,"risk_level":risk_level,
    }


def validate_behavior_lifecycle(events: list[dict]) -> str | None:
    """Require a minimally complete, server-recorded drag interaction.

    This is intentionally a structural gate, not a claim that browser events
    alone prove humanness. In active transport mode it prevents a correct
    answer without a drag lifecycle from silently bypassing the step-up path.
    """
    if not events:
        return "behavior_batches_missing"
    types = [event.get("type") for event in events]
    if types[0] != "challenge_loaded":
        return "behavior_lifecycle_missing_load"
    if types[-1] != "submit":
        return "behavior_lifecycle_missing_submit"
    required = ("pointer_down", "pointer_move", "drop", "selection_add")
    for event_type in required:
        if event_type not in types:
            return f"behavior_lifecycle_missing_{event_type}"
    timestamps = [int(event["timestamp_ms"]) for event in events]
    if any(current < previous for previous, current in zip(timestamps, timestamps[1:])):
        return "behavior_lifecycle_timestamp_invalid"
    return None


def validate_behavior_action_binding(
    events: list[dict],
    challenge_objects: list[dict],
    selected_object_ids: set[str],
) -> str | None:
    """Bind each submitted selection to a plausible object-drag lifecycle.

    The frontend's drop zone sits outside the image stage, so its final pointer
    coordinates cannot be compared to the image bounding boxes reliably. The
    source press can be checked server-side: it must start inside the selected
    object's issued hit region and be followed by drag, drop and selection.
    """
    objects = {str(row["temporary_object_id"]): row for row in challenge_objects}
    for object_id in selected_object_ids:
        source = objects.get(object_id)
        if source is None:
            return "behavior_action_unknown_object"
        x0 = float(source["bbox_x"])
        y0 = float(source["bbox_y"])
        x1 = x0 + float(source["bbox_width"])
        y1 = y0 + float(source["bbox_height"])

        press_index = next(
            (
                index
                for index, event in enumerate(events)
                if event.get("type") == "pointer_down" and event.get("object_id") == object_id
            ),
            None,
        )
        if press_index is None:
            return "behavior_action_binding_missing"
        press = events[press_index]
        x, y = press.get("x"), press.get("y")
        if x is None or y is None or not (x0 <= float(x) <= x1 and y0 <= float(y) <= y1):
            return "behavior_action_start_outside_source"

        required_after_press = ("drag_start", "drop", "selection_add")
        previous_index = press_index
        for event_type in required_after_press:
            next_index = next(
                (
                    index
                    for index, event in enumerate(events[previous_index + 1 :], start=previous_index + 1)
                    if event.get("type") == event_type and event.get("object_id") == object_id
                ),
                None,
            )
            if next_index is None:
                return "behavior_action_binding_missing"
            previous_index = next_index
    return None


def trusted_duration_ms(events: list[BehaviorEvent], fallback_duration_ms: int) -> int:
    """Prefer the receipt-validated event span over a separate payload field.

    Event timestamps originate in the browser, so this remains a behavioral
    feature rather than an authoritative clock. Server receipt cadence is
    evaluated separately by ``detect_batch_delivery_timing``.
    """
    loaded_at = next((event.timestamp_ms for event in events if event.type == "challenge_loaded"), None)
    submitted_at = next((event.timestamp_ms for event in reversed(events) if event.type == "submit"), None)
    if loaded_at is None or submitted_at is None or submitted_at < loaded_at:
        return fallback_duration_ms
    return max(100, min(180000, submitted_at - loaded_at))


def detect_batch_delivery_timing(
    events: list[BehaviorEvent], received_at: list,
) -> dict[str, int | bool]:
    """Flag a long browser timeline uploaded as a short final server burst.

    The UI sends receipt-chained batches every 200ms while events are being
    collected. A client-generated timeline can be forged, but a long trace
    sent in one short server-side burst has a measurable discrepancy. The
    signal asks for step-up only, preserving room for slow or interrupted
    network delivery.
    """
    client_span_ms = trusted_duration_ms(events, 0)
    if len(received_at) < 2:
        return {
            "detected": False,
            "client_event_span_ms": client_span_ms,
            "server_batch_span_ms": 0,
            "delivery_discrepancy_ms": 0,
            "batch_count": len(received_at),
        }

    server_span_ms = max(0, int((received_at[-1] - received_at[0]).total_seconds() * 1000))
    discrepancy_ms = max(0, client_span_ms - server_span_ms)
    detected = (
        client_span_ms >= 1500
        and discrepancy_ms >= 1200
        and server_span_ms <= max(500, int(client_span_ms * 0.45))
    )
    return {
        "detected": detected,
        "client_event_span_ms": client_span_ms,
        "server_batch_span_ms": server_span_ms,
        "delivery_discrepancy_ms": discrepancy_ms,
        "batch_count": len(received_at),
    }


def detect_stop_go_signal(events: list[BehaviorEvent]) -> dict[str, int | bool]:
    """Detect a narrow scripted stop/go and terminal-correction pattern.

    The signal is intentionally conservative: one pause alone is normal human
    behavior. It becomes suspicious only when a long stationary segment is
    followed immediately by a large restart and a small reversal near the end
    of the drag. The caller uses it for step-up, never a hard block.
    """
    points = [
        event
        for event in events
        if event.type in {"pointer_down", "drag_start", "pointer_move", "drop"}
        and event.x is not None
        and event.y is not None
    ]
    segments: list[tuple[float, float, float, int]] = []
    for previous, current in zip(points, points[1:]):
        dx = current.x - previous.x
        dy = current.y - previous.y
        segments.append((dx, dy, math.hypot(dx, dy), current.timestamp_ms - previous.timestamp_ms))

    pause_restart_count = 0
    for pause, restart in zip(segments, segments[1:]):
        _, _, pause_distance, pause_duration = pause
        _, _, restart_distance, restart_duration = restart
        if (
            pause_distance <= 0.015
            and pause_duration >= 300
            and restart_distance >= 0.20
            and 0 < restart_duration <= 350
        ):
            pause_restart_count += 1

    movement_segments = [segment for segment in segments if segment[2] >= 0.008]
    terminal_correction = False
    if len(movement_segments) >= 2:
        previous_dx, previous_dy, _, _ = movement_segments[-2]
        correction_dx, correction_dy, correction_distance, _ = movement_segments[-1]
        direction_dot = previous_dx * correction_dx + previous_dy * correction_dy
        terminal_correction = direction_dot < 0 and 0.01 <= correction_distance <= 0.08

    pointer_move_count = sum(event.type == "pointer_move" for event in events)
    sparse_pause_restart = pause_restart_count > 0 and pointer_move_count <= 5

    return {
        "detected": pause_restart_count > 0 and terminal_correction,
        "pause_restart_count": pause_restart_count,
        "terminal_correction": terminal_correction,
        "sparse_pause_restart": sparse_pause_restart,
    }


def queue_rows(view: str = "pending") -> list[dict]:
    path = settings.labeling_dir / ("relation_candidates_all.jsonl" if view in {"approved", "rejected", "all"} else "queue.jsonl")
    if not path.exists(): return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    reviewed = settings.labeling_dir / "reviewed.jsonl"
    latest: dict[str, dict] = {}
    if reviewed.exists():
        for line in reviewed.read_text(encoding="utf-8").splitlines():
            if line.strip():
                review = json.loads(line)
                latest[review["queue_id"]] = review
    pending = []
    for row in rows:
        review = latest.get(row["queue_id"])
        status = review.get("review_status") if review else "pending"
        if view == "pending" and status in {"approved", "rejected"}: continue
        if view in {"approved", "rejected"} and status != view: continue
        pending.append(review if review else row)
    return pending


def load_queue_candidates() -> list[dict]:
    """검수 대상 후보 원본(queue.jsonl)을 필터 없이 그대로 읽는다."""
    path = settings.labeling_dir / "queue.jsonl"
    if not path.exists(): return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def find_candidate(queue_id: str) -> dict | None:
    for row in load_queue_candidates():
        if str(row["queue_id"]) == queue_id: return row
    for row in queue_rows("all"):
        if str(row["queue_id"]) == queue_id: return row
    return None


def candidate_state(row: dict, decisions: dict, active_ids: set) -> str | None:
    """후보의 확정 상태: 'approved'/'rejected'/'labeled' 또는 None(미검수)."""
    qid = str(row["queue_id"])
    d = decisions.get(qid)
    if d and d.get("review_status") in ("approved", "rejected", "labeled", "needs_revision"):
        return d["review_status"]
    # 레거시: DB에는 이미 활성 등록됐지만 결정 기록이 없는 승인분
    ex = row.get("existing_question_id")
    if ex and str(ex) in active_ids: return "approved"
    if f"tq_{row.get('question_id')}" in active_ids: return "approved"
    return None


def append_final_manifest(question: dict, objects: list[dict]) -> None:
    # ★파일 경로가 아니라 자산 저장소를 거친다 — 로컬이면 예전과 같은 파일에 쓴다.
    raw = asset_store.read_text(MANIFEST_KEY)
    existing: dict[str, dict] = {}
    if raw:
        for line in raw.splitlines():
            if line.strip():
                row = json.loads(line)
                existing[row["challenge_id"]] = row
    existing[question["id"]] = {
        "challenge_id": question["id"], "source": question["source"],
        "source_question_id": question["source_question_id"],
        "image_path": question["image_path"], "instruction": question["instruction_ko"],
        "difficulty": question["difficulty"], "review_status": "approved",
        "objects": [{"object_key": row["object_key"], "label": row["label"],
                     "bbox": [row["x"], row["y"], row["width"], row["height"]],
                     "role": row["role"], "piece_path": row.get("piece_path")} for row in objects],
    }
    asset_store.write_text(MANIFEST_KEY, "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in existing.values()))


@asynccontextmanager
async def lifespan(_: FastAPI):
    # ★자산 폴더는 로컬 백엔드일 때만 만든다. object 면 버킷에 있으므로 만들 폴더가 없다.
    paths = [settings.labeling_dir, settings.runtime_dir / "attempts",
             settings.runtime_dir / "behavior-events", settings.runtime_dir / "logs"]
    if settings.asset_storage_backend == "local":
        paths = [settings.final_dir / "images", settings.final_dir / "pieces"] + paths
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
    # ★설정이 틀렸으면 기동할 때 바로 터뜨린다. 첫 요청까지 미루면 원인이 멀어진다.
    asset_store.get_asset_storage()
    database.initialize()
    # ① Origin 검증은 ALLOWED_ORIGINS가 실도메인일 때만 발동. 기본값 '*'면 조용히 무력화되므로
    # 실서비스에서 놓치지 않도록 시작 시 경고를 남긴다(설정은 배포 .env에서).
    if not settings.allowed_origins or "*" in settings.allowed_origins:
        import sys
        print("[SECURITY] ALLOWED_ORIGINS=* → Origin 검증 비활성 상태입니다. "
              "실서비스에서는 캡차 서빙 도메인을 ALLOWED_ORIGINS에 설정하세요.", file=sys.stderr)
    yield


app = FastAPI(title="CatChap Object Drag CAPTCHA", version="2.0.0", docs_url="/docs", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=list(settings.allowed_origins), allow_credentials=False,
                   allow_methods=["GET", "POST", "PUT", "OPTIONS"], allow_headers=["*"])

# IP당 분당 요청 상한 — 대량요청 봇(다운로드/크롤러/API 플러드/애플리케이션 홍수) 차단.
_rate_hits: dict[str, deque] = {}


def _apply_security_headers(response):
    """② 임베드 위젯용 방어 헤더. 프레이밍은 전면차단이 아니라 허용목록(frame-ancestors)."""
    emb = settings.embed_origins
    frame_ancestors = "*" if "*" in emb else (" ".join(("'self'", *emb)) if emb else "'self'")
    response.headers["Content-Security-Policy"] = f"frame-ancestors {frame_ancestors}"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    if request.url.path.startswith("/health"):
        return await call_next(request)
    ip = client_ip(request); now = time.monotonic()
    dq = _rate_hits.setdefault(ip, deque())
    while dq and now - dq[0] > 60: dq.popleft()
    if len(dq) >= settings.rate_limit_per_minute:
        return _apply_security_headers(JSONResponse({"detail": "Too many requests"}, status_code=429))
    dq.append(now)
    if len(_rate_hits) > 20000:  # 메모리 보호: 비활성 IP 정리
        for k in [k for k, v in list(_rate_hits.items()) if not v or now - v[-1] > 120]: _rate_hits.pop(k, None)
    return _apply_security_headers(await call_next(request))


@app.get("/health/live")
def live(): return {"status": "ok"}


@app.get("/health/ready")
def ready():
    database_ready = database.ping()
    # ms added a cheap existence check; prefer it over fetching a full question.
    approved_questions = database.has_active_question() if database_ready else False
    ai_readiness = behavior_ai.readiness()
    behavior_required = settings.behavior_event_transport != "off"
    ai_policy_matches = (
        not behavior_required
        or ai_readiness.policy_mode == settings.behavior_policy_mode
    )
    service_ready = (
        database_ready
        and approved_questions
        and (not behavior_required or (ai_readiness.ready and ai_policy_matches))
    )
    return {
        "status": "ok" if service_ready else "error",
        "database_ready": database_ready,
        "approved_questions": approved_questions,
        "behavior_policy_mode": settings.behavior_policy_mode,
        "behavior_event_transport": settings.behavior_event_transport,
        "behavior_ai_policy_matches": ai_policy_matches,
        "behavior_ai": ai_readiness.as_dict(),
    }


@app.get("/api/config")
def public_config():
    # ③ 프론트가 postMessage targetOrigin을 이 목록으로만 좁히도록 서버가 허용 임베드 출처를 내려준다.
    return {"siteKey": settings.site_key, "embedOrigins": list(settings.embed_origins)}


@app.post("/api/captcha/challenges", status_code=status.HTTP_201_CREATED)
def create_challenge(payload: ChallengeCreate, request: Request, x_captcha_site_key: str | None = Header(None)):
    require_header(x_captcha_site_key, settings.site_key, "Invalid site key"); check_origin(request)
    ip_hash=hash_value(client_ip(request)); pattern=database.request_pattern(payload.session_id,ip_hash)
    if pattern["ip_challenges_1m"]>=settings.max_challenges_per_minute:
        raise HTTPException(429,"Too many CAPTCHA requests")
    if pattern["session_telemetry_failures_10m"]>=settings.max_telemetry_failures_10m:
        raise HTTPException(429,"Too many invalid behavior attempts")
    question = database.active_question()
    if not question: raise HTTPException(503, "No approved CAPTCHA questions")
    challenge_id = str(uuid.uuid4()); now = utcnow(); expires = now + timedelta(seconds=settings.challenge_ttl_seconds)
    behavior_nonce = secrets.token_urlsafe(24)
    # 적응형 PoW: 최근 실패/과다요청 세션엔 난이도 상향 → 봇 재시도 비용 계단식 상승.
    pow_bits = settings.pow_difficulty_bits
    if settings.pow_enabled and (pattern["session_failures_10m"] >= settings.pow_stepup_failures
                                 or pattern["session_challenges_10m"] >= settings.pow_stepup_challenges):
        pow_bits += settings.pow_stepup_bits
    rng = secrets.SystemRandom()
    mappings = [(obj["id"], f"tmp_{secrets.token_urlsafe(8)}") for obj in question["objects"] if obj["role"] != "invalid"]
    temporary = {object_id: temp for object_id, temp in mappings}
    objects = [{"object_id":temporary[obj["id"]], "hit_region":[obj["bbox_x"],obj["bbox_y"],obj["bbox_width"],obj["bbox_height"]],
                "preview_url":f"/api/captcha/assets/{challenge_id}/{temporary[obj['id']]}"}
               for obj in question["objects"] if obj["id"] in temporary]
    # ② 허니팟: 빈 영역에 투명 함정 히트영역 추가. 사람은 안 건드리고 열거 봇만 집음 → 제출 시 봇 확정.
    honeypot_ids = []
    boxes = [(float(o["bbox_x"]),float(o["bbox_y"]),float(o["bbox_width"]),float(o["bbox_height"])) for o in question["objects"]]
    for _ in range(settings.honeypot_count):
        hp = _place_honeypot(boxes, rng)
        if not hp: continue
        tid = f"tmp_{secrets.token_urlsafe(8)}"; honeypot_ids.append(tid); boxes.append(hp)
        objects.append({"object_id":tid,"hit_region":list(hp),"preview_url":f"/api/captcha/assets/{challenge_id}/{tid}"})
    rng.shuffle(objects)
    database.create_challenge({"id":challenge_id,"question_id":question["id"],"session_id":payload.session_id,
        "purpose":payload.purpose,"lecture_id":payload.lecture_id,"expires_at":expires,"created_at":now,
        "client_ip_hash":ip_hash,"pow_bits":pow_bits,"honeypot_ids":json.dumps(honeypot_ids) if honeypot_ids else None},
        mappings, behavior_nonce)
    # ③ 드롭존 랜덤화: 위치·너비를 챌린지마다 바꿔 좌표 하드코딩 봇을 무력화(드래그 방식 동일).
    dz_x = round(rng.uniform(0.0, 0.35), 3); dz_w = round(rng.uniform(0.55, 0.80), 3)
    response = {"challenge_id":challenge_id,"type":"object_drag","instruction":question["instruction_ko"],
            "image_url":f"/api/captcha/assets/{challenge_id}/image","width":question["image_width"],
            "height":question["image_height"],"objects":objects,
            "drop_zone":{"x":dz_x,"y":0.0,"width":dz_w,"height":1.0},"expires_at":expires.isoformat()+"Z",
            "behavior_event_transport":settings.behavior_event_transport,
            "behavior_batch_interval_ms":200,"behavior_batch_max_events":32}
    if settings.behavior_event_transport != "off":
        response["behavior_nonce"] = behavior_nonce
    if settings.pow_enabled:
        # 연산 퍼즐: 클라이언트가 사람이 문제를 푸는 동안(수 초) 백그라운드로 해결 → 체감 0.
        response["pow"] = {"seed": challenge_id, "bits": pow_bits}
    return response


@app.get("/api/captcha/assets/{challenge_id}/{asset_id}")
def challenge_asset(challenge_id: str, asset_id: str):
    challenge = database.challenge_for_verify(challenge_id)
    if not challenge: raise HTTPException(404, "Challenge not found")
    question = database.get_question(challenge["question_id"])
    if question is None: raise HTTPException(404, "Question not found")
    if asset_id == "image": return asset_store.asset_response(question["image_path"])
    mapping = next((m for m in challenge["objects"] if m["temporary_object_id"] == asset_id), None)
    if not mapping: raise HTTPException(404, "Asset not found")
    if not mapping.get("piece_path"): raise HTTPException(404, "Piece not found")
    return asset_store.asset_response(mapping["piece_path"])


@app.post("/api/captcha/challenges/{challenge_id}/behavior-batches")
def collect_behavior_batch(
    challenge_id: str,
    payload: BehaviorBatchRequest,
    request: Request,
    x_captcha_site_key: str | None = Header(None),
):
    """Store a short behavior batch before answer verification.

    ``nonce`` and the receipt chain bind batches to one issued challenge. The
    final verify endpoint deliberately scores only these server-side records.
    """
    # ① 세 엔드포인트(create_challenge·verify·여기)의 Origin 검사가 일관돼야 한다.
    # 배치만 빠져 있으면 허용목록을 켜도 이 경로로 들어올 수 있다.
    require_header(x_captcha_site_key, settings.site_key, "Invalid site key"); check_origin(request)
    if settings.behavior_event_transport == "off":
        raise HTTPException(409, "behavior_transport_disabled")
    challenge = database.challenge_for_verify(challenge_id)
    if not challenge or challenge["session_id"] != payload.session_id:
        raise HTTPException(404, "Challenge not found")
    if challenge["status"] != "issued" or challenge["expires_at"] <= utcnow():
        raise HTTPException(409, "Challenge is no longer active")
    try:
        receipt = database.append_behavior_batch(
            challenge_id=challenge_id,
            nonce=payload.nonce,
            batch_seq=payload.batch_seq,
            previous_receipt=payload.previous_receipt,
            events=[event.model_dump() for event in payload.events],
        )
    except ValueError as error:
        detail = str(error)
        status_code = 403 if detail == "behavior_nonce_invalid" else 409
        raise HTTPException(status_code, detail) from error
    return {"accepted": True, **receipt}


@app.post("/api/captcha/challenges/{challenge_id}/verify")
def verify(challenge_id: str, payload: VerifyRequest, request: Request,
           x_captcha_site_key: str | None = Header(None)):
    require_header(x_captcha_site_key, settings.site_key, "Invalid site key"); check_origin(request)
    verify_received_at = utcnow()
    challenge = database.challenge_for_verify(challenge_id)
    if not challenge or challenge["session_id"] != payload.session_id: raise HTTPException(404, "Challenge not found")
    if challenge["status"] == "passed": raise HTTPException(409, "Challenge already used")
    if challenge["expires_at"] <= verify_received_at: raise HTTPException(410, "Challenge expired")
    if challenge["attempt_count"] >= settings.max_attempts: raise HTTPException(429, "No attempts remaining")
    # 형식 자체가 잘못된 요청은 PoW 검사보다 먼저 거른다. 12개 리스트 중복 검사가
    # sha256 보다 싸고, 이건 PoW 를 풀었든 안 풀었든 422 인 요청이다.
    if len(payload.selected_object_ids) != len(set(payload.selected_object_ids)):
        raise HTTPException(422, "Duplicate selected object")
    _pow_bits = int(challenge.get("pow_bits") or settings.pow_difficulty_bits)  # 발급 시 정한 난이도(적응형)
    if settings.pow_enabled and not pow_verify(challenge_id, payload.pow_nonce, _pow_bits):
        # 연산 퍼즐 미해결 = 정당한 클라이언트 비용을 치르지 않은 요청. 정답/행동 채점 이전에 저렴하게 차단.
        return {"success":False,"pow_failed":True}
    submitted=set(payload.selected_object_ids)
    # ⑤ 허니팟: 함정 히트영역을 하나라도 집었으면 = 열거/자동화 봇 확정. 단 여기서 즉시 return하지 않고
    # 행동 채점·기록까지 통과시켜 '확정 봇 궤적'(가장 부족한 라벨 데이터)을 확보한 뒤 차단한다.
    # 어차피 봇이므로 사용자 영향은 0.
    honeypots=set(json.loads(challenge["honeypot_ids"])) if challenge.get("honeypot_ids") else set()
    hit_honeypot=bool(submitted & honeypots)
    targets={o["temporary_object_id"] for o in challenge["objects"] if o["role"]=="target"}
    valid={o["temporary_object_id"] for o in challenge["objects"]}; correct=submitted==targets and submitted <= valid
    reason="honeypot" if hit_honeypot else (None if correct else ("unknown_object" if not submitted<=valid else "incorrect_selection"))
    question = database.get_question(challenge["question_id"])
    if question is None:
        raise HTTPException(404, "Question not found")

    behavior_id = behavior_attempt_id(challenge_id, int(challenge["attempt_count"]) + 1)
    if settings.behavior_event_transport == "off":
        server_events, telemetry_reason = [], None
        batch_received_at = []
        prediction = BehaviorPrediction(behavior_id, "disabled", "behavior_transport_off")
    else:
        server_events, telemetry_reason = database.trusted_behavior_events(challenge_id)
        if telemetry_reason is None:
            telemetry_reason = validate_behavior_lifecycle(server_events)
        if telemetry_reason is None:
            telemetry_reason = validate_behavior_action_binding(server_events, challenge["objects"], submitted)
        if telemetry_reason:
            predict_payload, predict_reason = None, telemetry_reason
        else:
            predict_payload, predict_reason = build_predict_payload(
                attempt_id=behavior_id,
                challenge_id=challenge_id,
                session_id=payload.session_id,
                events=server_events,
                width=int(question["image_width"]),
                height=int(question["image_height"]),
                retry_count=int(challenge["attempt_count"]),
                presented_at=challenge.get("created_at"),
                submitted_at=verify_received_at,
                # 코드가 오면 그쪽이 이기고, 없으면 build_predict_payload 가 session_id 로 폴백한다.
                anonymous_participant_id=payload.participant_id,
            )
            telemetry_reason = predict_reason
        prediction = behavior_ai.score(predict_payload, behavior_id, predict_reason)
        batch_received_at = database.behavior_batch_received_at(challenge_id) if telemetry_reason is None else []
    main_verdict = "passed" if correct else "failed"
    final_verdict, enforced_action = resolve_final_verdict(
        captcha_correct=correct,
        prediction=prediction,
        local_policy_mode=settings.behavior_policy_mode,
    )

    current_ip_hash=hash_value(client_ip(request)); pattern=database.request_pattern(payload.session_id,current_ip_hash)
    summary_events = [BehaviorEvent.model_validate(event) for event in server_events]
    attempt_duration_ms = trusted_duration_ms(summary_events, payload.duration_ms)
    stop_go_signal = detect_stop_go_signal(summary_events)
    batch_delivery_timing = detect_batch_delivery_timing(summary_events, batch_received_at)
    summary=summarize(summary_events,submitted,targets,attempt_duration_ms,correct,pattern,
                      current_ip_hash!=challenge["client_ip_hash"])
    summary["stop_go_signal"] = stop_go_signal
    summary["batch_delivery_timing"] = batch_delivery_timing

    # During shadow rollout we retain the CAPTCHA outcome but record the
    # missing/invalid telemetry. In active mode it must receive a step-up.
    # A narrow stop/go pattern is also step-up only: it is a supplemental
    # server-side signal while the candidate model remains in shadow mode.
    enforcement_reason = telemetry_reason
    if correct and settings.behavior_event_transport == "active":
        if telemetry_reason:
            final_verdict, enforced_action = "failed", "step_up"
        elif stop_go_signal["detected"]:
            final_verdict, enforced_action = "failed", "step_up"
            enforcement_reason = "behavior_stop_go_pattern"
        elif stop_go_signal["sparse_pause_restart"]:
            final_verdict, enforced_action = "failed", "step_up"
            enforcement_reason = "behavior_sparse_pause_restart_pattern"
        elif batch_delivery_timing["detected"]:
            final_verdict, enforced_action = "failed", "step_up"
            enforcement_reason = "behavior_batch_delivery_timing"

    event_dir=settings.runtime_dir/"behavior-events"/utcnow().strftime("%Y/%m/%d"); event_dir.mkdir(parents=True,exist_ok=True)
    event_file=event_dir/f"{challenge_id}-{challenge['attempt_count']+1}.json"
    event_file.write_text(json.dumps({"challenge_id":challenge_id,"behavior_attempt_id":behavior_id,
        "events":server_events,"telemetry_reason":telemetry_reason,
        "submitted_browser_event_count":len(payload.events),"answer_correct":correct,
        "behavior_summary":summary},ensure_ascii=False),encoding="utf-8")
    captcha_attempt_id = database.record_attempt(
        challenge_id, list(submitted), correct, reason, attempt_duration_ms, summary,
        str(event_file.relative_to(settings.runtime_dir)),
    )
    database.record_behavior_shadow_prediction(
        captcha_attempt_id, prediction, settings.behavior_policy_mode, main_verdict, final_verdict,
    )
    if settings.behavior_policy_mode == "shadow":
        # This request proves in the behavior-service DB that its recommendation
        # did not alter the main CAPTCHA result.
        prediction = behavior_ai.record_shadow_outcome(prediction, main_verdict)

    debug_payload = None
    if settings.behavior_debug_response:
        debug_payload = {
            "status": prediction.status,
            "detail": prediction.detail,
            "human_score": prediction.human_score,
            "bot_risk_score": prediction.bot_risk_score,
            "risk_score": prediction.risk_score,
            "risk_level": prediction.risk_level,
            "recommended_action": prediction.recommended_action,
            "model_name": prediction.model_name,
            "model_version": prediction.model_version,
            "reasons": prediction.reasons,
            "telemetry_reason": telemetry_reason,
            "enforcement_reason": enforcement_reason,
            "stop_go_signal": stop_go_signal,
            "batch_delivery_timing": batch_delivery_timing,
        }
    # ⑤ 봇 궤적을 행동채점(shadow)·기록에 흘려보낸 뒤에 차단한다. 확정 봇의 궤적은 우리가 가장
    # 부족한 라벨 데이터인데, 채점 전에 return 하면 그게 버려진다. 어차피 봇이라 사용자 영향 0.
    if hit_honeypot:
        response = {"success":False,"blocked":True,"risk_level":"automated","reason":"honeypot"}
        if debug_payload is not None:
            response["behavior_debug"] = debug_payload
        return response
    if not correct:
        response = {"success":False,"remaining_attempts":max(0,settings.max_attempts-challenge["attempt_count"]-1)}
        if debug_payload is not None:
            response["behavior_debug"] = debug_payload
        return response
    if enforced_action == "step_up_and_rate_limit":
        response = {"success":False,"blocked":True,"risk_level":prediction.risk_level}
        if debug_payload is not None:
            response["behavior_debug"] = debug_payload
        return response
    if enforced_action == "step_up":
        response = {"success":False,"step_up":True,"risk_level":prediction.risk_level}
        if debug_payload is not None:
            response["behavior_debug"] = debug_payload
        return response
    # ms 의 규칙 기반 게이트. 행동 AI 게이트(resolve_final_verdict)는 위에서 이미 적용됐다.
    auto=automation_score(payload.client_signals); risk_total=summary["risk_score"]+auto
    signature=summary["behavior_signature"]; database.record_fingerprint(payload.session_id,signature,risk_total)
    def _gated(body: dict) -> dict:
        if debug_payload is not None:
            body["behavior_debug"] = debug_payload
        return body
    if risk_total>=settings.behavior_block_score:
        return _gated({"success":False,"blocked":True,"risk_level":"automated" if auto>=60 else summary["risk_level"]})
    if risk_total>=settings.behavior_step_up_score:
        return _gated({"success":False,"step_up":True,"risk_level":summary["risk_level"]})
    # 클러스터 게이트: 같은 행동 지문을 여러 세션이 공유 = 공유 풀이툴로 판단해 차단.
    if database.signature_cluster_size(signature, settings.cluster_window_hours) >= settings.cluster_block_size:
        return _gated({"success":False,"blocked":True,"risk_level":"automated","reason":"tool_cluster"})
    token=secrets.token_urlsafe(32); database.create_token(challenge_id,hash_value(token),challenge["purpose"],payload.session_id,
                                                         utcnow()+timedelta(seconds=settings.verification_ttl_seconds),challenge.get("lecture_id"))
    response = {"success":True,"captcha_token":token,"expires_in":settings.verification_ttl_seconds}
    if debug_payload is not None:
        response["behavior_debug"] = debug_payload
    return response


@app.post("/api/verify-token")
def verify_token(payload: VerifyTokenRequest, x_captcha_site_secret: str | None = Header(None)):
    """서버-투-서버 토큰 검증. 호스트(인강) 서버가 사이트 시크릿으로 호출한다."""
    require_header(x_captcha_site_secret, settings.site_secret, "Invalid site secret")
    result = database.verify_token(hash_value(payload.token), payload.purpose, payload.session_id, payload.lecture_id)
    if not result:
        return {"success": False, "error": "invalid_or_used_token"}
    return {"success": True, "lecture_id": result.get("lecture_id"), "challenge_id": result.get("challenge_id")}


@app.post("/api/signup", status_code=201)
def signup(payload: SignupRequest):
    if not database.consume_token(hash_value(payload.captcha_token),"signup",payload.session_id):
        raise HTTPException(403,"CAPTCHA_REQUIRED")
    salt=os.urandom(16); digest=hashlib.scrypt(payload.password.encode(),salt=salt,n=2**14,r=8,p=1)
    database.create_user(str(uuid.uuid4()),str(payload.email),f"scrypt${salt.hex()}${digest.hex()}")
    return {"created":True}


@app.get("/api/admin/queue")
def admin_queue(view: Literal["pending", "approved", "rejected", "all"] = "pending",
                x_captcha_admin_key: str | None = Header(None)):
    reviewer=require_admin(x_captcha_admin_key)
    decisions=database.decision_map(); active_ids=database.active_question_ids()
    counts=database.review_counts()
    candidates=load_queue_candidates()
    if view=="pending":
        others=database.others_claimed_ids(reviewer)
        rows=[r for r in candidates
              if candidate_state(r,decisions,active_ids) not in ("approved","rejected")
              and str(r["queue_id"]) not in others]
        # 한 번도 검수 안 한 항목(결정 기록 없음)을 먼저, 임시저장 등은 뒤로.
        rows.sort(key=lambda r: 1 if str(r["queue_id"]) in decisions else 0)
        items=rows
    elif view in ("approved","rejected"):
        items=[r for r in candidates if candidate_state(r,decisions,active_ids)==view]
    else:
        items=candidates
    return {"items":items,"view":view,"reviewer":reviewer,"counts":counts}


@app.post("/api/admin/claim/{queue_id}")
def claim_item(queue_id: str, x_captcha_admin_key: str | None = Header(None)):
    """현재 보고 있는 항목을 잠깐 선점(하트비트). blocked=True면 다른 사람이 처리했거나 보는 중."""
    reviewer=require_admin(x_captcha_admin_key)
    held=database.touch_claim(queue_id, reviewer)
    dec=database.get_decision(queue_id)
    decided=bool(dec and dec.get("review_status") in ("approved","rejected"))
    return {"held":held,"decided":decided,"blocked":(not held) or decided}


@app.get("/api/admin/counts")
def admin_counts(x_captcha_admin_key: str | None = Header(None)):
    """전원 합산 승인/제외 개수(DB 기준). 실시간 폴링용."""
    require_admin(x_captcha_admin_key)
    return database.review_counts()


@app.get("/api/admin/clusters")
def admin_clusters(hours: int = Query(default=24, ge=1, le=720),
                   min_sessions: int = Query(default=5, ge=2, le=1000),
                   x_captcha_admin_key: str | None = Header(None)):
    """공유 풀이툴 의심 클러스터: 같은 행동 지문을 쓰는 세션이 많은 순."""
    require_admin(x_captcha_admin_key)
    return {"window_hours": hours, "block_threshold": settings.cluster_block_size,
            "clusters": database.top_signature_clusters(hours, min_sessions)}


@app.get("/api/admin/behavior-shadow")
def admin_behavior_shadow(days: int = Query(default=7, ge=1, le=90), x_captcha_admin_key: str | None = Header(None)):
    """behavior-AI active 승격 준비도. shadow 예측을 집계해 go/no-go를 판정한다."""
    require_admin(x_captcha_admin_key)
    s = database.behavior_shadow_summary(days)
    min_passed = settings.behavior_promote_min_passed; max_fp = settings.behavior_promote_max_fp_rate
    fp = s.get("fp_proxy_rate"); would_block = s.get("would_block", 0)
    # 봇 저지 바닥: 아무것도 안 잡는 모델은 FP 0%로 통과하므로 would_block>0을 필수 조건에 추가(sw 지적 ②).
    ready = bool(s.get("table") and s.get("passed", 0) >= min_passed
                 and fp is not None and fp <= max_fp and would_block > 0)
    if not s.get("table"):
        verdict = "no_data"; reason = "behavior_shadow_predictions 테이블 없음(모델 미연동)"
    elif s.get("passed", 0) < min_passed:
        verdict = "insufficient_data"; reason = f"사람 프록시 표본 {s.get('passed',0)} < 최소 {min_passed}"
    elif would_block == 0:
        verdict = "no_detection"; reason = "모델이 아무것도 저지하지 않음(would_block=0) — 봇 탐지 무효"
    elif fp is not None and fp > max_fp:
        verdict = "fp_too_high"; reason = f"오탐 프록시 {fp:.2%} > 허용 {max_fp:.2%}"
    else:
        verdict = "ready"; reason = "관측 기준 충족 — 단, 최종 go/no-go는 참여자 단위 집계로(런북 참조)"
    # ⚠️ 시도 단위 거친 관측 지표. 최종 판단은 participant_id 기반 참여자별 FRR로(BEHAVIOR_AI_PROMOTION.md).
    return {"summary": s, "criteria": {"min_passed": min_passed, "max_fp_rate": max_fp, "min_would_block": 1},
            "ready": ready, "verdict": verdict, "reason": reason,
            "granularity": "attempt-level (rough observational)",
            "current_policy_mode": settings.behavior_policy_mode}


@app.get("/api/admin/exposure")
def admin_exposure(limit: int = Query(default=50, ge=1, le=500), x_captcha_admin_key: str | None = Header(None)):
    """노출(출제 횟수) 상위 문항 = 캐싱 위험 '탄' 후보. 회전/은퇴 판단용."""
    require_admin(x_captcha_admin_key)
    return {"items": database.top_exposed(limit)}


@app.post("/api/admin/rest/{question_id}")
def admin_rest(question_id: str, x_captcha_admin_key: str | None = Header(None)):
    """과다 노출 문항을 출제 풀에서 내린다(rested)."""
    require_admin(x_captcha_admin_key)
    return {"rested": database.rest_question(question_id)}


@app.get("/api/admin/assets/{path:path}")
def admin_asset(path: str, x_captcha_admin_key: str | None = Header(None)):
    require_admin(x_captcha_admin_key)
    return FileResponse(safe_asset(settings.labeling_dir,path))


@app.put("/api/admin/reviews/{queue_id}")
def save_review(queue_id: str, payload: ReviewRequest, x_captcha_admin_key: str | None = Header(None)):
    reviewer=require_admin(x_captcha_admin_key)
    item=find_candidate(queue_id)
    if not item or payload.queue_id!=queue_id: raise HTTPException(404,"Queue item not found")
    # 선점 우선: 이미 다른 검수자가 승인/제외한 문항이면 막는다.
    prior=database.get_decision(queue_id)
    if prior and prior["review_status"] in {"approved","rejected"} and prior["reviewer"]!=reviewer:
        raise HTTPException(409, f"이미 다른 검수자({prior['reviewer']})가 처리한 문항입니다.")
    expected=payload.expected_target_count if payload.expected_target_count is not None else int(item["expected_target_count"])
    targets=sum(o.role=="target" for o in payload.objects)
    if payload.review_status=="approved" and (targets!=expected or any(o.role=="ambiguous" for o in payload.objects)):
        raise HTTPException(422,"Approved labels must match expected target count and contain no ambiguous objects")
    existing_question_id=item.get("existing_question_id")
    question_id=(existing_question_id if existing_question_id else f"tq_{item['question_id']}") if payload.review_status=="approved" else None
    # 원자적 선점 저장(DB 단일 소스): 먼저 잡은 사람만 통과.
    if not database.record_decision(queue_id, payload.review_status, reviewer, question_id):
        raise HTTPException(409, "이미 다른 검수자가 처리한 문항입니다.")
    now=utcnow(); review={**item,**payload.model_dump(),"reviewer":reviewer,"reviewed_at":now.isoformat()+"Z"}
    reviewed=settings.labeling_dir/"reviewed.jsonl"
    with reviewed.open("a",encoding="utf-8") as fp: fp.write(json.dumps(review,ensure_ascii=False)+"\n")
    if payload.review_status=="approved":
        existing_question_id=item.get("existing_question_id")
        if existing_question_id:
            existing=database.get_question(existing_question_id)
            if not existing: raise HTTPException(404,"Existing question not found")
            existing_objects={str(obj["object_key"]):obj for obj in existing["objects"]}
            object_rows=[]
            for obj in payload.objects:
                original=existing_objects.get(str(obj.object_key))
                object_rows.append({**obj.model_dump(),"piece_path":original.get("piece_path") if original else None})
            question={**existing,"instruction_ko":payload.instruction_ko,"difficulty":payload.difficulty,
                "status":"active","review_status":"approved","reviewer":reviewer,"reviewed_at":now}
            database.upsert_question(question,object_rows)
            append_final_manifest(question,object_rows)
            return {"saved":True,"status":payload.review_status}
        question_id=f"tq_{item['question_id']}"; image_source=settings.labeling_dir/item["image_path"]
        # ★자산은 저장소를 거친다(로컬이면 예전과 같은 파일에 쓴다).
        image_key=f"images/{question_id}{image_source.suffix.lower()}"
        image_bytes=image_source.read_bytes(); asset_store.write_bytes(image_key,image_bytes)
        from PIL import Image
        with Image.open(io.BytesIO(image_bytes)) as image: width,height=image.size
        object_rows=[];prepared={str(row.get("object_key")):row for row in item.get("objects",[])}
        for obj in payload.objects:
            piece_rel=None
            if obj.role in {"target","decoy"}:
                piece_rel=f"pieces/{question_id}-{obj.object_key}.png"
                original=prepared.get(str(obj.object_key));prepared_path=original.get("prepared_piece_path") if original else None
                prepared_source=settings.labeling_dir/prepared_path if prepared_path else None
                if prepared_source and prepared_source.is_file():
                    unchanged=all(abs(float(getattr(obj,name))-float(original.get(name,0)))<1e-6 for name in ("x","y","width","height"))
                    if unchanged:
                        asset_store.write_bytes(piece_rel,prepared_source.read_bytes())
                    else:
                        new_box=(round(obj.x*width),round(obj.y*height),round((obj.x+obj.width)*width),round((obj.y+obj.height)*height))
                        old_box=(round(float(original["x"])*width),round(float(original["y"])*height),round((float(original["x"])+float(original["width"]))*width),round((float(original["y"])+float(original["height"]))*height))
                        new_size=(max(1,new_box[2]-new_box[0]),max(1,new_box[3]-new_box[1]));old_size=(max(1,old_box[2]-old_box[0]),max(1,old_box[3]-old_box[1]))
                        with Image.open(prepared_source) as source_piece:
                            masked=source_piece.convert("RGBA")
                            if masked.size!=old_size: masked=masked.resize(old_size,Image.Resampling.LANCZOS)
                            adjusted=Image.new("RGBA",new_size,(0,0,0,0));adjusted.alpha_composite(masked,(old_box[0]-new_box[0],old_box[1]-new_box[1]))
                            buf=io.BytesIO();adjusted.save(buf,"PNG",optimize=True);asset_store.write_bytes(piece_rel,buf.getvalue())
                else:
                    with Image.open(io.BytesIO(image_bytes)) as image:
                        box=(round(obj.x*width),round(obj.y*height),round((obj.x+obj.width)*width),round((obj.y+obj.height)*height))
                        buf=io.BytesIO();image.crop(box).convert("RGBA").save(buf,"PNG",optimize=True);asset_store.write_bytes(piece_rel,buf.getvalue())
            object_rows.append({**obj.model_dump(),"piece_path":piece_rel})
        question={"id":question_id,"type":"object_drag","instruction_ko":payload.instruction_ko,
            "instruction_en":item.get("question_en"),"source":"tallyqa_visual_genome",
            "source_question_id":str(item["question_id"]),"image_path":image_key,
            "image_width":width,"image_height":height,"difficulty":payload.difficulty,"status":"active",
            "review_status":"approved","reviewer":reviewer,"reviewed_at":now,"created_at":now}
        database.upsert_question(question,object_rows)
        append_final_manifest(question, object_rows)
    # 검수를 끝냈으니 '보는 중' 잠금은 해제(결정 자체는 DB에 영구 저장됨).
    database.release_review_claim(queue_id,reviewer)
    return {"saved":True,"status":payload.review_status}


if settings.static_dir.exists():
    assets=settings.static_dir/"assets"
    if assets.exists(): app.mount("/assets",StaticFiles(directory=assets),name="assets")
    @app.get("/{path:path}",include_in_schema=False)
    def frontend(path: str):
        candidate=settings.static_dir/path
        if candidate.is_file():
            return FileResponse(candidate)
        # index.html은 캐시하지 않아 새 빌드(해시된 에셋)를 항상 즉시 반영한다.
        return FileResponse(settings.static_dir/"index.html",
                            headers={"Cache-Control":"no-cache, no-store, must-revalidate"})
