#!/usr/bin/env python3
"""레드팀 봇 라벨 데이터 생성기.

다양한 봇 프로파일이 실제 캡차 파이프라인(챌린지 → 배치채널 → PoW → verify)을 거치게 해서,
모델(behavior-AI)이 봇 궤적을 학습할 라벨 데이터를 만든다. 완벽비전 가정(정답 오라클)으로
정답은 맞히되 '행동'만 봇답게 한다.

식별: session_id = "rtbot-<유형>-<n>-<uuid8>" → 수집 후 이 접두어로 label='bot'을 붙인다.
      (sw의 ai_behavior_attempts에 라벨을 기록하는 건 sw와 조율. 라벨 SQL은 이 파일 하단 참고.)

사용법 (GPU에서, 앱 config 있는 dir에서):
    CAPTCHA_BASE_URL=http://127.0.0.1:8000 python3 scripts/redteam_bot_collect.py <프로파일당건수>

⚠️ 실서비스 배치채널(영수증체인) 필요 → sw-captcha가 떠 있는 :8000 대상으로 실행.
"""
from __future__ import annotations
import json, math, os, sys, time, uuid, hashlib, urllib.request, urllib.error
sys.path.insert(0, ".")
from app.config import settings
from app.db import Database

BASE = os.environ.get("CAPTCHA_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
SK = settings.site_key
db = Database(settings)


def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Captcha-Site-Key": SK})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode())
        except Exception: return e.code, {}


def lzb(d):
    n = 0
    for c in d:
        if c == 0: n += 8; continue
        n += 8 - c.bit_length(); break
    return n
def pow_solve(seed, bits):
    i = 0
    while True:
        if lzb(hashlib.sha256(f"{seed}:{i}".encode()).digest()) >= bits: return str(i)
        i += 1


def targets(cid):
    with db.connection(True) as c, c.cursor() as cur:
        cur.execute("SELECT m.temporary_object_id t FROM captcha_challenge_objects m "
                    "JOIN captcha_objects o ON o.id=m.object_id WHERE m.challenge_id=%s AND o.role='target'", (cid,))
        return [r["t"] for r in cur.fetchall()]


# ── 봇 프로파일: 시작점(히트영역 중앙)→정답존 사이 '궤적'만 다르게 ──
def ev(seq, t, oid=None, x=None, y=None, ts=0):
    return {"seq": seq, "type": t, "object_id": oid, "x": x, "y": y, "timestamp_ms": ts}

def build_events(kind, oid, hr, dz, now):
    sx, sy = hr[0] + hr[2] / 2, hr[1] + hr[3] / 2
    dx, dy = dz["x"] + dz["width"] / 2, 0.8
    e = [ev(0, "challenge_loaded", None, None, None, now)]
    t = now + 300; seq = 1
    e.append(ev(seq, "pointer_down", oid, sx, sy, t)); seq += 1
    e.append(ev(seq, "drag_start", oid, sx, sy, t)); seq += 1
    if kind == "teleport":
        t += 30; e.append(ev(seq, "pointer_move", oid, dx, dy, t)); seq += 1
    elif kind == "straight_uniform":
        for i in range(1, 9):
            f = i / 9; t += 40  # 등속·등간격
            e.append(ev(seq, "pointer_move", oid, sx + (dx - sx) * f, sy + (dy - sy) * f, t)); seq += 1
    elif kind == "robotic_regular":
        for i in range(1, 13):
            f = i / 13; t += 33  # 완벽 규칙 간격
            e.append(ev(seq, "pointer_move", oid, round(sx + (dx - sx) * f, 3), round(sy + (dy - sy) * f, 3), t)); seq += 1
    elif kind == "jitter_fast":
        for i in range(1, 11):
            f = i / 11; t += 18  # 빠르고 작은 지터
            jx = 0.006 * math.sin(i * 2.1); jy = 0.006 * math.cos(i * 1.7)
            e.append(ev(seq, "pointer_move", oid, sx + (dx - sx) * f + jx, sy + (dy - sy) * f + jy, t)); seq += 1
    else:  # smart_curve (사람 흉내)
        n = 14
        for i in range(1, n + 1):
            f = i / n; t += 30 + int(35 * abs(math.sin(f * 3.3 + i)))
            cx = sx + (dx - sx) * f + 0.05 * math.sin(f * math.pi)
            cy = sy + (dy - sy) * f - 0.04 * math.sin(f * math.pi)
            e.append(ev(seq, "pointer_move", oid, min(1, max(0, cx)), min(1, max(0, cy)), t)); seq += 1
    t += 40
    e.append(ev(seq, "drop", oid, dx, dy, t)); seq += 1
    e.append(ev(seq, "selection_add", oid, dx, dy, t)); seq += 1
    e.append(ev(seq, "submit", None, None, None, t + 120))
    return e


PROFILES = ["teleport", "straight_uniform", "robotic_regular", "jitter_fast", "smart_curve"]


def run_one(kind, n):
    sess = f"rtbot-{kind}-{n:04d}-{uuid.uuid4().hex[:8]}"
    st, ch = post("/api/captcha/challenges", {"purpose": "lecture", "session_id": sess, "lecture_id": "REDTEAM"})
    if not ch.get("challenge_id"): return "no_challenge"
    cid = ch["challenge_id"]; nonce = ch.get("behavior_nonce")
    tg = targets(cid)
    if not tg: return "no_targets"
    oid = tg[0]
    hr = next((o["hit_region"] for o in ch["objects"] if o["object_id"] == oid), [0.3, 0.4, 0.1, 0.1])
    dz = ch.get("drop_zone", {"x": 0.72, "width": 0.25})
    events = build_events(kind, oid, hr, dz, int(time.time() * 1000))
    # 배치 2개(영수증체인)
    half = len(events) // 2
    if nonce:
        st, b1 = post(f"/api/captcha/challenges/{cid}/behavior-batches",
                      {"session_id": sess, "nonce": nonce, "batch_seq": 0, "previous_receipt": None, "events": events[:half]})
        st, b2 = post(f"/api/captcha/challenges/{cid}/behavior-batches",
                      {"session_id": sess, "nonce": nonce, "batch_seq": 1, "previous_receipt": b1.get("receipt"), "events": events[half:]})
        if not (b1.get("accepted") and b2.get("accepted")): return f"batch_reject:{b1.get('accepted')},{b2.get('accepted')}"
    pown = pow_solve(ch["pow"]["seed"], ch["pow"]["bits"]) if ch.get("pow") else None
    time.sleep(0.5)
    st, res = post(f"/api/captcha/challenges/{cid}/verify",
                   {"selected_object_ids": tg, "session_id": sess, "duration_ms": events[-1]["timestamp_ms"],
                    "events": events, "pow_nonce": pown, "participant_id": sess})
    return "ok" if res.get("success") else ("step_up" if res.get("step_up") else json.dumps(res)[:40])


def main():
    per = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    print(f"=== 레드팀 봇 수집 | 프로파일 {len(PROFILES)}종 × {per}건 | 대상 {BASE} ===")
    tally = {}
    for kind in PROFILES:
        outs = {}
        for i in range(per):
            r = run_one(kind, i)
            outs[r] = outs.get(r, 0) + 1
            time.sleep(0.3)
        tally[kind] = outs
        print(f"  {kind}: {outs}")
    print("session_id 접두어 = 'rtbot-<유형>-' → 아래 SQL로 라벨(예):")
    print("  UPDATE catchap_ai.ai_behavior_attempts SET label='bot', label_source='redteam',")
    print("    bot_type=SUBSTRING_INDEX(SUBSTRING_INDEX(session_id,'-',2),'-',-1)")
    print("    WHERE session_id LIKE 'rtbot-%' AND label IS NULL;")


if __name__ == "__main__":
    main()
