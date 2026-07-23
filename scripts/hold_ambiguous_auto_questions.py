from __future__ import annotations

import json
import shutil
from pathlib import Path

from app.config import settings
from app.db import Database, utcnow


AMBIGUOUS_LABELS = ("man", "woman", "boy", "girl")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


def main() -> None:
    database = Database(settings)
    marks = ",".join(["%s"] * len(AMBIGUOUS_LABELS))
    with database.connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT DISTINCT q.id
              FROM captcha_questions q
              JOIN captcha_objects o ON o.question_id=q.id AND o.role='target'
             WHERE q.status='active'
               AND q.reviewer='auto-instance-mask-qc'
               AND o.label IN ({marks})
             ORDER BY q.id
            """,
            AMBIGUOUS_LABELS,
        )
        held_ids = [row["id"] for row in cur.fetchall()]
        if held_ids:
            for offset in range(0, len(held_ids), 500):
                chunk = held_ids[offset : offset + 500]
                placeholders = ",".join(["%s"] * len(chunk))
                cur.execute(
                    f"""
                    UPDATE captcha_questions
                       SET status='inactive',
                           review_status='needs_revision',
                           reviewer='auto-semantic-hold',
                           reviewed_at=%s
                     WHERE id IN ({placeholders})
                    """,
                    [utcnow(), *chunk],
                )
        conn.commit()

    held = set(held_ids)
    for name in ("challenges.jsonl", "clean_auto_challenges.jsonl"):
        path = settings.final_dir / name
        rows = read_jsonl(path)
        backup = settings.final_dir / f"{path.stem}.before-semantic-hold.jsonl"
        if path.exists() and not backup.exists():
            shutil.copy2(path, backup)
        write_jsonl(path, [row for row in rows if row.get("challenge_id") not in held])

    audit = settings.final_dir / "semantic_hold_questions.jsonl"
    write_jsonl(audit, [{"question_id": question_id, "reason": "ambiguous_gender_or_age_label"} for question_id in held_ids])
    print(json.dumps({"held": len(held_ids), "labels": AMBIGUOUS_LABELS}, ensure_ascii=False))


if __name__ == "__main__":
    main()
