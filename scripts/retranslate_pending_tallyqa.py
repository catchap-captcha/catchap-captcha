from __future__ import annotations

import json
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parent.parent
LABELING = ROOT / "data" / "labeling"
QUEUE = LABELING / "queue.jsonl"
ALL = LABELING / "relation_candidates_all.jsonl"
REVIEWED = LABELING / "reviewed.jsonl"
TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def question_to_command(question: str) -> str:
    text = re.sub(r"\s+", " ", question.strip()).rstrip(" ?.!")
    text = re.sub(r"^how many\s+", "", text, flags=re.I)
    text = re.sub(r"^of the\s+", "", text, flags=re.I)
    text = re.sub(r"\s+(?:are|is)\s+there$", "", text, flags=re.I)
    text = re.sub(r"\s+can you count$", "", text, flags=re.I)
    text = re.sub(r"^(.+?)\s+(are|is|have|has|do|does|did|can)\s+(.+)$", r"\1 that \2 \3", text, flags=re.I)
    return f"Move all {text} to the answer zone."


ZONE = (r"(?:정답\s*존|정답\s*영역|정답\s*구역|응답\s*지역|응답\s*영역|응답\s*구역|"
        r"답안\s*영역|답변\s*영역|해답\s*영역|답\s*영역|응답존)")


def _fix_josa(m: re.Match) -> str:
    ch = m.group(1)
    return ch + ("을" if (ord(ch) - 0xAC00) % 28 else "를")


def clean_korean(text: str) -> str:
    text = re.sub(r"\s+", " ", text.strip())
    text = re.sub(r"[.。]+$", "", text)
    # 목적격 조사 을/를 교정(존 표현 앞)
    text = re.sub(r"([가-힣])을(?=\s*(?:모두\s*)?" + ZONE + r")", _fix_josa, text)
    # 끝맺음 통일: '<존 표현>…' → '정답존으로 이동하시오.'
    if re.search(ZONE, text):
        text = re.sub(ZONE + r".*$", "정답존으로 이동하시오.", text)
    else:
        text = text.rstrip(" .") + " 정답존으로 이동하시오."
    return text


def translate(command: str) -> str:
    params = {"client": "gtx", "sl": "en", "tl": "ko", "dt": "t", "q": command}
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            response = requests.get(TRANSLATE_URL, params=params, timeout=20)
            response.raise_for_status()
            text = "".join(part[0] for part in response.json()[0] if part and part[0])
            cleaned = clean_korean(text)
            if len(cleaned) < 8 or re.search(r"[☆♦]", cleaned):
                raise ValueError(f"invalid translation: {cleaned}")
            return cleaned
        except Exception as error:
            last_error = error
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"translation failed: {command}") from last_error


def main() -> None:
    queue_rows = read_jsonl(QUEUE)
    all_rows = read_jsonl(ALL)
    latest: dict[str, dict] = {}
    for review in read_jsonl(REVIEWED):
        latest[str(review["queue_id"])] = review
    pending_ids = {
        str(row["queue_id"])
        for row in queue_rows
        if latest.get(str(row["queue_id"]), {}).get("review_status") not in {"approved", "rejected"}
    }
    # 조건 추출 실패로 '질문 조건에 맞는 …' 일반문구가 된 것만 재번역(정상 번역은 보존).
    pending = [row for row in queue_rows
               if str(row["queue_id"]) in pending_ids
               and "질문 조건에 맞는" in str(row.get("instruction_ko", ""))
               and str(row.get("question_en", "")).strip()]
    commands = [question_to_command(str(row.get("question_en", ""))) for row in pending]

    unique_commands = list(dict.fromkeys(commands))
    translated_commands: dict[str, str] = {}
    failed = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(translate, command): command for command in unique_commands}
        for future in as_completed(futures):
            command = futures[future]
            try:
                translated_commands[command] = future.result()
            except Exception:
                failed += 1  # 실패 건은 건너뜀(기존 문구 유지)
    translated = {str(row["queue_id"]): translated_commands[command]
                  for row, command in zip(pending, commands) if command in translated_commands}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    shutil.copy2(QUEUE, QUEUE.with_name(f"queue.before-retranslation-{stamp}.jsonl"))
    if ALL.exists():
        shutil.copy2(ALL, ALL.with_name(f"relation_candidates_all.before-retranslation-{stamp}.jsonl"))
    for row in queue_rows:
        if str(row["queue_id"]) in translated:
            row["instruction_ko"] = translated[str(row["queue_id"])]
            row["translation_status"] = "full_sentence_review_ready"
    for row in all_rows:
        if str(row.get("queue_id")) in translated:
            row["instruction_ko"] = translated[str(row["queue_id"])]
            row["translation_status"] = "full_sentence_review_ready"
    write_jsonl(QUEUE, queue_rows)
    write_jsonl(ALL, all_rows)
    print(json.dumps({"targeted": len(pending), "unique_commands": len(unique_commands),
                      "translated_unique": len(translated_commands), "failed_unique": failed,
                      "rows_updated": len(translated), "backup": stamp}, ensure_ascii=False))
    for row in pending[:15]:
        print(json.dumps({"question": row["question_en"], "command": question_to_command(row["question_en"]), "ko": translated[str(row["queue_id"])]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
