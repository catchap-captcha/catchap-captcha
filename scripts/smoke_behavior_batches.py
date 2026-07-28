"""Verify the server-side behavior batch flow after a new VPC deployment."""

from __future__ import annotations

import json
import os
import time
import urllib.request
import uuid

from app.config import settings
from app.db import Database


BASE_URL = os.getenv("CAPTCHA_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def post(path: str, payload: dict, site_key: str) -> dict:
    request = urllib.request.Request(
        BASE_URL + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Captcha-Site-Key": site_key},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    site_key = os.environ["CAPTCHA_SITE_KEY"]
    session_id = f"batch-smoke-{uuid.uuid4()}"
    challenge = post(
        "/api/captcha/challenges",
        {"purpose": "signup", "risk_level": "high", "session_id": session_id},
        site_key,
    )
    if challenge.get("behavior_event_transport") == "off":
        raise RuntimeError("Set BEHAVIOR_EVENT_TRANSPORT=shadow before running this smoke test")

    challenge_id = challenge["challenge_id"]
    objects = Database(settings).challenge_for_verify(challenge_id)["objects"]
    target_ids = [row["temporary_object_id"] for row in objects if row["role"] == "target"]
    now = int(time.time() * 1000)
    object_id = target_ids[0]
    events = [
        {"seq": 0, "type": "challenge_loaded", "object_id": None, "x": None, "y": None, "timestamp_ms": now},
        {"seq": 1, "type": "pointer_down", "object_id": object_id, "x": 0.2, "y": 0.2, "timestamp_ms": now + 120},
        {"seq": 2, "type": "pointer_move", "object_id": object_id, "x": 0.4, "y": 0.4, "timestamp_ms": now + 220},
        {"seq": 3, "type": "pointer_move", "object_id": object_id, "x": 0.65, "y": 0.62, "timestamp_ms": now + 340},
        {"seq": 4, "type": "drop", "object_id": object_id, "x": 0.85, "y": 0.8, "timestamp_ms": now + 460},
        {"seq": 5, "type": "selection_add", "object_id": object_id, "x": 0.85, "y": 0.8, "timestamp_ms": now + 460},
        {"seq": 6, "type": "submit", "object_id": None, "x": None, "y": None, "timestamp_ms": now + 560},
    ]
    first = post(
        f"/api/captcha/challenges/{challenge_id}/behavior-batches",
        {
            "session_id": session_id,
            "nonce": challenge["behavior_nonce"],
            "batch_seq": 0,
            "previous_receipt": None,
            "events": events[:4],
        },
        site_key,
    )
    second = post(
        f"/api/captcha/challenges/{challenge_id}/behavior-batches",
        {
            "session_id": session_id,
            "nonce": challenge["behavior_nonce"],
            "batch_seq": 1,
            "previous_receipt": first["receipt"],
            "events": events[4:],
        },
        site_key,
    )
    if not first.get("accepted") or not second.get("accepted"):
        raise RuntimeError(f"batch not accepted: {first}, {second}")

    time.sleep(0.7)

    result = post(
        f"/api/captcha/challenges/{challenge_id}/verify",
        {"selected_object_ids": target_ids, "session_id": session_id, "duration_ms": 700},
        site_key,
    )
    if not result.get("success"):
        raise RuntimeError(f"verification did not pass: {result}")
    print(json.dumps({"challenge_id": challenge_id, "batches": 2, "result": "passed"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
