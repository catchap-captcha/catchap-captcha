"""Local sink for the aim-segment experiment. Writes JSONL, nothing else.

Runs beside `vite dev`, which proxies /api to the live captcha and /collect-aim
here. The captcha server never sees these events — its batch validator only
accepts known types, and slipping a new one in would get the whole batch
rejected, which would break the solve we are trying to observe.

Why the experiment exists
-------------------------
Recording starts at `pointer_down` today. Measured on the 2026-08-06 collection:
19.2 events per session, of which 0.2 fall outside a drag — the aiming segment is
discarded almost entirely. A drag is ~12 points, and 12 points do not separate one
person from another (human pairs reach path similarity 1.0000), which is why the
replay families still pass 58.6% after every model-side fix.

The aiming segment is the one part a replay attacker cannot reuse: it depends on
where this question put its objects, and that changes every time.

    .venv/bin/python scripts/aim_collector.py --out data/aim/aim_20260808.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SAFE_CODE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
MAX_BODY = 2 << 20          # 2MB. An aim segment is a few KB; anything larger is a bug.


class Handler(BaseHTTPRequestHandler):
    out_path: Path
    seen: dict

    def _reply(self, code: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        # The page is served from vite on another port during development.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(payload)

    def do_OPTIONS(self) -> None:            # noqa: N802
        self._reply(204, {})

    def do_POST(self) -> None:               # noqa: N802
        if self.path != "/collect-aim":
            self._reply(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._reply(400, {"error": "bad length"})
            return
        if length <= 0 or length > MAX_BODY:
            self._reply(400, {"error": "bad length"})
            return

        try:
            record = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._reply(400, {"error": "bad json"})
            return

        code = record.get("participant_id")
        if not code or not SAFE_CODE.match(str(code)):
            # An unlabelled trace cannot be split by person later, and every
            # promotion criterion is per person. Refuse rather than store junk.
            self._reply(400, {"error": "participant_id 가 없거나 형식이 맞지 않는다"})
            return

        events = record.get("aim_events") or []
        if not isinstance(events, list) or not events:
            self._reply(400, {"error": "aim_events 가 비었다"})
            return

        record["received_at"] = datetime.now(timezone.utc).isoformat()
        record["aim_event_count"] = len(events)
        with self.out_path.open("a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        person = str(code).split("-")[0]
        self.seen[person] = self.seen.get(person, 0) + 1
        total = sum(self.seen.values())
        print(f"  {code:22s} 조준 {len(events):3d}개  ·  누적 {total}건  "
              f"{dict(sorted(self.seen.items()))}", flush=True)
        self._reply(200, {"ok": True, "stored": len(events)})

    def log_message(self, *args) -> None:    # noqa: D102
        pass                                  # the per-record line above is enough


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path,
                    default=Path("data/aim/aim_20260808.jsonl"))
    ap.add_argument("--port", type=int, default=18100)
    args = ap.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    Handler.out_path = args.out
    Handler.seen = {}

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"조준 구간 수집기 · http://127.0.0.1:{args.port}/collect-aim")
    print(f"기록 -> {args.out}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"\n중지. 총 {sum(Handler.seen.values())}건 · {Handler.seen}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
