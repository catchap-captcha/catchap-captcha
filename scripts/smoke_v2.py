from __future__ import annotations

import json
import os
import time
import urllib.request
import uuid


BASE = os.getenv("CAPTCHA_BASE_URL", "http://127.0.0.1:8000")


def request(path: str, method: str = "GET", payload: dict | None = None, headers: dict | None = None):
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=body, method=method, headers={"Content-Type":"application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=10) as response:
        return response.status, response.headers.get("Content-Type"), response.read()


def main() -> None:
    site_key=os.environ["CAPTCHA_SITE_KEY"]; admin_key=os.environ["CAPTCHA_ADMIN_KEY"]; session_id=str(uuid.uuid4())
    status,_,body=request("/api/captcha/challenges","POST",{"purpose":"signup","risk_level":"high","session_id":session_id},{"X-Captcha-Site-Key":site_key})
    challenge=json.loads(body); assert status==201 and challenge["objects"]
    image_status,image_type,_=request(challenge["image_url"]); assert image_status==200 and image_type.startswith("image/")
    for obj in challenge["objects"]:
        piece_status,piece_type,_=request(obj["preview_url"]); assert piece_status==200 and piece_type.startswith("image/")
    now=int(time.time()*1000)
    events=[{"type":"challenge_loaded","timestamp_ms":now},{"type":"pointer_down","object_id":challenge["objects"][0]["object_id"],"x":.2,"y":.2,"timestamp_ms":now+300},{"type":"drag_start","object_id":challenge["objects"][0]["object_id"],"x":.2,"y":.2,"timestamp_ms":now+310},{"type":"pointer_move","object_id":challenge["objects"][0]["object_id"],"x":.5,"y":.5,"timestamp_ms":now+600},{"type":"drop","object_id":challenge["objects"][0]["object_id"],"x":.8,"y":.8,"timestamp_ms":now+900}]
    status,_,body=request(f"/api/captcha/challenges/{challenge['challenge_id']}/verify","POST",{"selected_object_ids":[obj["object_id"] for obj in challenge["objects"]],"session_id":session_id,"duration_ms":1200,"events":events},{"X-Captcha-Site-Key":site_key})
    result=json.loads(body); assert status==200 and result["success"] and len(result["captcha_token"])>=32
    status,_,body=request("/api/admin/queue",headers={"X-Captcha-Admin-Key":admin_key}); queue=json.loads(body); assert status==200 and "items" in queue
    print(json.dumps({"challenge":"passed","assets":"passed","token":"passed","admin_queue":len(queue["items"])},ensure_ascii=False))


if __name__=="__main__": main()
