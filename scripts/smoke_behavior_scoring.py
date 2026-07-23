from __future__ import annotations

import json
import time
import urllib.request

from app.config import settings
from app.db import Database


BASE="http://127.0.0.1:8000"
SITE_KEY=""


def post(path: str, body: dict) -> dict:
    global SITE_KEY
    if not SITE_KEY:
        with urllib.request.urlopen(BASE+"/api/config") as response: SITE_KEY=json.load(response)["siteKey"]
    request=urllib.request.Request(BASE+path,data=json.dumps(body).encode(),method="POST",
        headers={"Content-Type":"application/json","X-Captcha-Site-Key":SITE_KEY})
    with urllib.request.urlopen(request) as response: return json.load(response)


def target_ids(challenge_id: str) -> list[str]:
    challenge=Database(settings).challenge_for_verify(challenge_id)
    return [obj["temporary_object_id"] for obj in challenge["objects"] if obj["role"]=="target"]


def human_events(ids: list[str], base: int) -> tuple[list[dict],int]:
    events=[{"type":"challenge_loaded","object_id":None,"x":None,"y":None,"timestamp_ms":base}];t=base+1200
    for index,object_id in enumerate(ids):
        events.extend([
            {"type":"object_enter","object_id":object_id,"x":.2,"y":.2,"timestamp_ms":t},
            {"type":"pointer_down","object_id":object_id,"x":.2,"y":.2,"timestamp_ms":t+500},
            {"type":"drag_start","object_id":object_id,"x":.2,"y":.2,"timestamp_ms":t+500},
            {"type":"pointer_move","object_id":object_id,"x":.27,"y":.31,"timestamp_ms":t+620},
            {"type":"pointer_move","object_id":object_id,"x":.41,"y":.37,"timestamp_ms":t+790},
            {"type":"pointer_move","object_id":object_id,"x":.56,"y":.51,"timestamp_ms":t+1020},
            {"type":"pointer_move","object_id":object_id,"x":.73,"y":.63,"timestamp_ms":t+1300},
            {"type":"drop","object_id":object_id,"x":.9,"y":.82,"timestamp_ms":t+1550},
            {"type":"selection_add","object_id":object_id,"x":.9,"y":.82,"timestamp_ms":t+1550},
            {"type":"object_leave","object_id":object_id,"x":.9,"y":.82,"timestamp_ms":t+1560},
        ]);t+=1900
    events.append({"type":"submit","object_id":None,"x":None,"y":None,"timestamp_ms":t})
    return events,t-base


def bot_events(ids: list[str], base: int) -> tuple[list[dict],int]:
    events=[{"type":"challenge_loaded","object_id":None,"x":None,"y":None,"timestamp_ms":base}];t=base+100
    for object_id in ids:
        events.extend([
            {"type":"pointer_down","object_id":object_id,"x":.2,"y":.2,"timestamp_ms":t},
            {"type":"drag_start","object_id":object_id,"x":.2,"y":.2,"timestamp_ms":t},
            {"type":"pointer_move","object_id":object_id,"x":.4,"y":.4,"timestamp_ms":t+50},
            {"type":"pointer_move","object_id":object_id,"x":.6,"y":.6,"timestamp_ms":t+100},
            {"type":"pointer_move","object_id":object_id,"x":.8,"y":.8,"timestamp_ms":t+150},
            {"type":"drop","object_id":object_id,"x":1.0,"y":1.0,"timestamp_ms":t+200},
            {"type":"selection_add","object_id":object_id,"x":1.0,"y":1.0,"timestamp_ms":t+200},
        ]);t+=250
    events.append({"type":"submit","object_id":None,"x":None,"y":None,"timestamp_ms":t})
    return events,max(100,t-base)


def run(kind: str) -> dict:
    session=f"behavior-smoke-{kind}-{int(time.time()*1000)}"
    challenge=post("/api/captcha/challenges",{"purpose":"login","risk_level":"high","session_id":session})
    ids=target_ids(challenge["challenge_id"]);base=int(time.time()*1000)
    events,duration=human_events(ids,base) if kind=="human" else bot_events(ids,base)
    return post(f"/api/captcha/challenges/{challenge['challenge_id']}/verify",
        {"selected_object_ids":ids,"session_id":session,"duration_ms":duration,"events":events})


def main() -> None:
    human=run("human");bot=run("bot")
    if not human.get("success"): raise RuntimeError(f"human simulation failed: {human}")
    if not bot.get("step_up"): raise RuntimeError(f"bot simulation did not step up: {bot}")
    print(json.dumps({"human":human,"bot":bot},ensure_ascii=False))


if __name__=="__main__": main()
