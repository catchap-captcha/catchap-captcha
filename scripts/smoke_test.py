from __future__ import annotations

import json
import urllib.error
import urllib.request

from app.config import settings
from app.db import Database


BASE_URL = f"http://127.0.0.1:{settings.app_port}"


def request_json(
    path: str,
    method: str = "GET",
    payload: dict | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict]:
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        BASE_URL + path,
        method=method,
        data=body,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def main() -> None:
    db = Database(settings)
    status, ready = request_json("/health/ready")
    assert status == 200 and ready["status"] == "ok", (status, ready)
    print(f"ready: database=yes, images={ready['images']}")

    status, challenge = request_json(
        "/v1/challenges",
        method="POST",
        headers={"X-Captcha-Site-Key": settings.site_key},
    )
    assert status == 201, (status, challenge)
    challenge_id = challenge["challenge_id"]
    row = db.get_challenge(challenge_id)
    assert row is not None
    print("challenge: created and persisted")

    if challenge["mode"] == "question_answer":
        expected = str(row["expected_answer"])
        wrong = str(int(expected) + 7) if expected.isdigit() else "wrong-answer"
        status, rejected = request_json(
            f"/v1/challenges/{challenge_id}/verify",
            method="POST",
            payload={"answer": wrong, "duration_ms": 500},
            headers={"X-Captcha-Site-Key": settings.site_key},
        )
        assert status == 200 and not rejected["success"], (status, rejected)
        assert rejected["error"] == "answer_mismatch", rejected
        print("challenge: incorrect answer rejected")
        verify_payload = {"answer": expected, "duration_ms": 700}
    elif challenge["mode"] == "object_drag":
        boxes = json.loads(row["target_boxes"])
        box = boxes[0]
        target_x = box["x"] + box["width"] // 2
        target_y = box["y"] + box["height"] // 2
        start_x, start_y = 38, challenge["height"] - 38
        points = [
            {"x": start_x, "y": start_y, "t": 0},
            {"x": (start_x + target_x) // 2, "y": (start_y + target_y) // 2, "t": 270},
            {"x": target_x, "y": target_y, "t": 540},
        ]
        verify_payload = {
            "x": target_x,
            "y": target_y,
            "duration_ms": 540,
            "movements": points,
        }
        wrong_payload = {
            "x": 600,
            "y": 320,
            "duration_ms": 540,
            "movements": [
                {"x": start_x, "y": start_y, "t": 0},
                {"x": 320, "y": 321, "t": 270},
                {"x": 600, "y": 320, "t": 540},
            ],
        }
        status, rejected = request_json(
            f"/v1/challenges/{challenge_id}/verify",
            method="POST",
            payload=wrong_payload,
            headers={"X-Captcha-Site-Key": settings.site_key},
        )
        assert status == 200 and not rejected["success"], (status, rejected)
        assert rejected["error"] == "position_mismatch", rejected
        print("challenge: incorrect giraffe rejected")
    else:
        target_x = int(row["target_x"])
        points = [
            {"x": 0, "t": 0},
            {"x": target_x // 3, "t": 180},
            {"x": (target_x * 2) // 3, "t": 360},
            {"x": target_x, "t": 540},
        ]
        verify_payload = {
            "x": target_x,
            "duration_ms": 540,
            "movements": points,
        }
    status, verified = request_json(
        f"/v1/challenges/{challenge_id}/verify",
        method="POST",
        payload=verify_payload,
        headers={"X-Captcha-Site-Key": settings.site_key},
    )
    assert status == 200 and verified["success"], (status, verified)
    print("challenge: answer verified")

    status, consumed = request_json(
        "/v1/siteverify",
        method="POST",
        payload={"token": verified["verification_token"]},
        headers={"X-Captcha-Secret": settings.site_secret},
    )
    assert status == 200 and consumed["success"], (status, consumed)
    print("siteverify: one-time token consumed")

    status, replay = request_json(
        "/v1/siteverify",
        method="POST",
        payload={"token": verified["verification_token"]},
        headers={"X-Captcha-Secret": settings.site_secret},
    )
    assert status == 200 and not replay["success"], (status, replay)
    assert replay["error"] in {"already_consumed", "invalid_token"}
    print("siteverify: replay rejected")


if __name__ == "__main__":
    main()
