#!/usr/bin/env python3
"""레드팀 봇 라벨 데이터 생성기.

다양한 봇 프로파일이 실제 캡차 파이프라인(챌린지 → 배치채널 → PoW → verify)을 거치게 해서,
모델(behavior-AI)이 봇 궤적을 학습할 라벨 데이터를 만든다. 완벽비전 가정(정답 오라클)으로
정답은 맞히되 '행동'만 봇답게 한다.

타이밍 축(sw 지적): 모델이 잡는 건 궤적 모양이 아니라 '비현실적으로 빠른 타이밍'일 수 있다.
  --timing synthetic : 산술 타임스탬프, 전체 <1초 (기존 5종, 대조군)
  --timing real      : 실제 sleep + 벽시계, 사람 속도 수 초 (현실적 자동화 근사)
궤적(waypoints)은 동일, 타이밍만 축으로 분리 → 무엇이 판별에 기여하는지 대조.

식별: session_id = "rtbot-<유형>-<n>-<uuid8>" → 접두어로 label='bot' 부여.
      (sw ai_behavior_attempts 라벨은 sw 컨벤션대로: label_source='<생성기><YYYYMMDD>')

사용법 (GPU, 앱 config 있는 dir에서):
    CAPTCHA_BASE_URL=http://127.0.0.1:8000 python3 scripts/redteam_bot_collect.py [건수] [--timing synthetic|real]

⚠️ 실서비스 배치채널 필요 → sw-captcha가 떠 있는 :8000 대상.
"""
from __future__ import annotations
import json, math, os, random, sys, time, uuid, hashlib, urllib.request, urllib.error
sys.path.insert(0, ".")


def _load_env(path=".env"):
    """.env를 os.environ로 선주입. config.site_key가 import 시점 os.getenv라서,
    수동 실행 땐 .env가 안 올라와 데모키 기본값이 잡히는 문제를 막는다(기존 값은 유지)."""
    try:
        with open(path) as f:
            for line in f:
                s = line.strip()
                if s and not s.startswith("#") and "=" in s:
                    k, v = s.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass


_load_env()
from app.config import settings
from app.db import Database

BASE = os.environ.get("CAPTCHA_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
NOPOW = "--nopow" in sys.argv  # 서버 PoW 게이트 실측용: pow_nonce=null로 제출
SK = settings.site_key
db = Database(settings)
PROFILES = ["teleport", "straight_uniform", "robotic_regular", "jitter_fast", "smart_curve"]


def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Captcha-Site-Key": SK})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
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


def ev(seq, t, oid=None, x=None, y=None, ts=0):
    return {"seq": seq, "type": t, "object_id": oid, "x": x, "y": y, "timestamp_ms": ts}


def waypoints(kind, sx, sy, dx, dy):
    """(x, y, synth_interval_ms) — pointer_down → drop 사이 move들. 궤적 모양은 프로파일 고유."""
    if kind == "teleport":
        return [(dx, dy, 30)]
    if kind == "straight_uniform":
        return [(sx + (dx - sx) * i / 9, sy + (dy - sy) * i / 9, 40) for i in range(1, 9)]
    if kind == "robotic_regular":
        return [(round(sx + (dx - sx) * i / 13, 3), round(sy + (dy - sy) * i / 13, 3), 33) for i in range(1, 13)]
    if kind == "jitter_fast":
        return [(sx + (dx - sx) * i / 11 + 0.006 * math.sin(i * 2.1),
                 sy + (dy - sy) * i / 11 + 0.006 * math.cos(i * 1.7), 18) for i in range(1, 11)]
    wps = []  # smart_curve
    for i in range(1, 15):
        f = i / 14
        cx = min(1, max(0, sx + (dx - sx) * f + 0.05 * math.sin(f * math.pi)))
        cy = min(1, max(0, sy + (dy - sy) * f - 0.04 * math.sin(f * math.pi)))
        wps.append((cx, cy, 30 + int(35 * abs(math.sin(f * 3.3 + i)))))
    return wps


def build_events(kind, drag_objs, dz, timing, rng):
    """궤적=프로파일 고유, 타이밍=축(synthetic/real). drag_objs=[(oid,hit_region),...] 를 순서대로 끈다.
    상호작용 규모 축 검증용: 1개만 끌기(one) vs 여러 개 끌기(all)."""
    real = (timing == "real")
    dx, dy = dz.get("x", 0.72) + dz.get("width", 0.25) / 2, 0.8

    def advance(prev, interval_ms):
        if real:
            time.sleep(interval_ms / 1000.0)
            return int(time.time() * 1000)   # 벽시계 = 이벤트 타임스탬프 일치
        return prev + interval_ms

    t = int(time.time() * 1000)
    e = [ev(0, "challenge_loaded", None, None, None, t)]; seq = 1
    for (oid, hr) in drag_objs:
        sx, sy = hr[0] + hr[2] / 2, hr[1] + hr[3] / 2
        t = advance(t, rng.randint(500, 1200) if real else 250)   # 다음 객체로 이동/리딩
        e.append(ev(seq, "pointer_down", oid, sx, sy, t)); seq += 1
        e.append(ev(seq, "drag_start", oid, sx, sy, t)); seq += 1
        for (x, y, synth_iv) in waypoints(kind, sx, sy, dx, dy):
            iv = rng.randint(55, 160) if real else synth_iv       # real=사람 속도 이동간격
            t = advance(t, iv)
            e.append(ev(seq, "pointer_move", oid, x, y, t)); seq += 1
        t = advance(t, rng.randint(70, 180) if real else 40)
        e.append(ev(seq, "drop", oid, dx, dy, t)); seq += 1
        e.append(ev(seq, "selection_add", oid, dx, dy, t)); seq += 1
    e.append(ev(seq, "submit", None, None, None, advance(t, 120)))
    return e


def run_one(kind, n, timing, drag, rng):
    # 필드: rtbot-<유형>-<타이밍>-<드래그>-<n>-<uuid8> → 유형=idx2·타이밍=idx3·드래그=idx4
    sess = f"rtbot-{kind}-{timing}-{drag}-{n:04d}-{uuid.uuid4().hex[:8]}"
    st, ch = post("/api/captcha/challenges", {"purpose": "lecture", "session_id": sess, "lecture_id": "REDTEAM"})
    if not ch.get("challenge_id"): return "no_challenge"
    cid = ch["challenge_id"]; nonce = ch.get("behavior_nonce")
    tg = targets(cid)
    if not tg: return "no_targets"
    hrmap = {o["object_id"]: o["hit_region"] for o in ch["objects"]}
    if drag == "all":  # 규모 축: 전 객체를 끈다(이벤트만↑). 제출은 정답 타겟만 → 허니팟 회피·allow 가능.
        drag_objs = [(o["object_id"], o["hit_region"]) for o in ch["objects"]]
    else:              # one: 타겟 1개만 끈다.
        drag_objs = [(tg[0], hrmap.get(tg[0], [0.3, 0.4, 0.1, 0.1]))]
    sel = tg           # 두 모드 모두 정답(타겟)만 제출 → 궤적·타이밍 고정, 규모만 축으로 분리
    events = build_events(kind, drag_objs, ch.get("drop_zone", {}), timing, rng)  # real이면 실제 수초 소요
    if nonce:
        prev = None    # ≤32 이벤트/배치 제한 준수 → 30씩 청킹, 영수증 체인
        for bseq, i in enumerate(range(0, len(events), 30)):
            st, b = post(f"/api/captcha/challenges/{cid}/behavior-batches",
                         {"session_id": sess, "nonce": nonce, "batch_seq": bseq,
                          "previous_receipt": prev, "events": events[i:i + 30]})
            if not b.get("accepted"): return "batch_reject"
            prev = b.get("receipt")
    pown = None if NOPOW else (pow_solve(ch["pow"]["seed"], ch["pow"]["bits"]) if ch.get("pow") else None)
    dur = max(100, events[-1]["timestamp_ms"] - events[0]["timestamp_ms"])
    st, res = post(f"/api/captcha/challenges/{cid}/verify",
                   {"selected_object_ids": sel, "session_id": sess, "duration_ms": dur,
                    "events": events, "pow_nonce": pown, "participant_id": sess})
    return "ok" if res.get("success") else ("step_up" if res.get("step_up") else json.dumps(res)[:24])


def main():
    args = sys.argv[1:]
    timing = "synthetic"; drag = "one"; only = None
    if "--timing" in args:
        i = args.index("--timing"); timing = args[i + 1]; del args[i:i + 2]
    if "--drag" in args:   # 상호작용 규모 축: one(1개) | all(전 객체)
        i = args.index("--drag"); drag = args[i + 1]; del args[i:i + 2]
    if "--only" in args:   # 특정 프로파일만(통제실험 속도)
        i = args.index("--only"); only = args[i + 1]; del args[i:i + 2]
    if "--nopow" in args:  # 서버 PoW 게이트 실측(전역 NOPOW로 이미 잡힘)
        args.remove("--nopow")
    per = int(args[0]) if args else 3
    profiles = [p for p in PROFILES if only is None or p == only]
    rng = random.Random(12345)
    print(f"=== 레드팀 봇 수집 | {len(profiles)}종 × {per}건 | timing={timing} | drag={drag} | 대상 {BASE} ===")
    for kind in profiles:
        outs = {}
        for i in range(per):
            r = run_one(kind, i, timing, drag, rng)
            outs[r] = outs.get(r, 0) + 1
            if timing != "real": time.sleep(0.3)   # real은 이미 수초 소요
        print(f"  {kind}: {outs}")
    print(f"\nsession_id = rtbot-<유형>-<타이밍>-<n>-<uuid8> → 라벨(label_source는 sw 컨벤션대로):")
    print("  UPDATE catchap_ai.ai_behavior_attempts SET label='bot',")
    print(f"    label_source=CONCAT('ms_profiles_',SUBSTRING_INDEX(SUBSTRING_INDEX(session_id,'-',3),'-',-1),'_{time.strftime('%Y%m%d')}'),")
    print("    bot_type=SUBSTRING_INDEX(SUBSTRING_INDEX(session_id,'-',2),'-',-1)")
    print("    WHERE session_id LIKE 'rtbot-%' AND label IS NULL;")
    print("  -- 유형=idx2, 타이밍(synthetic|real)=idx3. label_source 예: ms_profiles_real_20260731")


if __name__ == "__main__":
    main()
