"""기존 지시문을 '…정답존으로 이동하시오.' 스타일로 통일하고 을/를 조사를 교정한다.

- DB captcha_questions.instruction_ko 일괄 갱신
- data/labeling 의 queue.jsonl / relation_candidates_all.jsonl 의 instruction_ko 갱신(백업 생성)

실행: .venv/bin/python scripts/restyle_instructions.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import settings
from app.db import Database


def _fix_josa(m: re.Match) -> str:
    ch = m.group(1)
    return ch + ("을" if (ord(ch) - 0xAC00) % 28 else "를")


# '정답존'의 다양한 표기(정답 존, 응답지역, 응답 영역 등)를 모두 포함
ZONE = (r"(?:정답\s*존|정답\s*영역|정답\s*구역|응답\s*지역|응답\s*영역|응답\s*구역|"
        r"답안\s*영역|답변\s*영역|해답\s*영역|답\s*영역|응답존)")


def restyle(text: str) -> str:
    if not text:
        return text
    text = text.strip()
    # 목적격 조사 을/를 교정 (존 표현 앞의 '…을')
    text = re.sub(r"([가-힣])을(?=\s*(?:모두\s*)?" + ZONE + r")", _fix_josa, text)
    # 끝맺음 통일: '<존 표현>…(옮기세요/이동…)' → '정답존으로 이동하시오.'
    text = re.sub(ZONE + r".*$", "정답존으로 이동하시오.", text)
    return text


def restyle_db() -> int:
    db = Database(settings)
    changed = 0
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, instruction_ko FROM captcha_questions")
        rows = cur.fetchall()
        for r in rows:
            new = restyle(r["instruction_ko"])
            if new != r["instruction_ko"]:
                cur.execute("UPDATE captcha_questions SET instruction_ko=%s WHERE id=%s", (new, r["id"]))
                changed += 1
        conn.commit()
    print(f"  DB captcha_questions: {changed}/{len(rows)} 변경")
    return changed


def restyle_file(path: Path) -> int:
    if not path.exists():
        print(f"  (없음) {path.name}")
        return 0
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    out = []
    changed = 0
    for l in lines:
        row = json.loads(l)
        if isinstance(row.get("instruction_ko"), str):
            new = restyle(row["instruction_ko"])
            if new != row["instruction_ko"]:
                row["instruction_ko"] = new
                changed += 1
        out.append(json.dumps(row, ensure_ascii=False))
    if changed:
        backup = path.with_suffix(path.suffix + ".before-restyle")
        if not backup.exists():
            backup.write_text("\n".join(lines) + "\n", encoding="utf-8")
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"  {path.name}: {changed}/{len(lines)} 변경")
    return changed


if __name__ == "__main__":
    print("=== 지시문 스타일 통일 (…정답존으로 이동하시오.) ===")
    restyle_db()
    lab = settings.labeling_dir
    for name in ("queue.jsonl", "relation_candidates_all.jsonl", "reviewed.jsonl"):
        restyle_file(lab / name)
    print("완료")
