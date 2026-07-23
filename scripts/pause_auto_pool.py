from __future__ import annotations

import json
import shutil

from app.config import settings
from app.db import Database


AUTO_SOURCES = ("train2014", "val2014", "VG_100K", "VG_100K_2")


def main() -> None:
    database = Database(settings)
    marks = ",".join(["%s"] * len(AUTO_SOURCES))
    with database.connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE captcha_questions SET status='inactive' "
            f"WHERE id LIKE 'auto\\_%%' AND source IN ({marks}) AND status='active'",
            AUTO_SOURCES,
        )
        paused = cur.rowcount
        conn.commit()

    manifest = settings.final_dir / "challenges.jsonl"
    rows = [json.loads(line) for line in manifest.read_text().splitlines() if line.strip()]
    backup = settings.final_dir / "challenges.before-auto-pause.jsonl"
    if not backup.exists():
        shutil.copy2(manifest, backup)
    kept = [row for row in rows if not str(row.get("challenge_id", "")).startswith("auto_")]
    manifest.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in kept))
    (settings.final_dir / "clean_auto_challenges.jsonl").write_text("")
    print(json.dumps({"paused_auto": paused, "active_manifest": len(kept)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
