from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter, defaultdict, deque

from app.config import settings
from app.db import Database


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=172)
    args = parser.parse_args()
    database = Database(settings)
    with database.connection(True) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT q.*,
                   (SELECT o.label FROM captcha_objects o
                     WHERE o.question_id=q.id AND o.role='target' ORDER BY o.id LIMIT 1) target_label
              FROM captcha_questions q
             WHERE q.status='inactive'
               AND q.review_status='approved'
               AND q.reviewer='auto-instance-mask-qc'
             ORDER BY q.id
        """)
        questions = cur.fetchall()
        for question in questions:
            cur.execute("SELECT * FROM captcha_objects WHERE question_id=%s AND role IN ('target','decoy') ORDER BY id", (question["id"],))
            question["objects"] = cur.fetchall()

    buckets: dict[str, deque] = defaultdict(deque)
    for question in questions:
        buckets[str(question["target_label"])].append(question)
    selected=[]
    labels=sorted(buckets, key=lambda label:(len(buckets[label]),label))
    while len(selected)<args.count and labels:
        remaining=[]
        for label in labels:
            if buckets[label] and len(selected)<args.count:
                selected.append(buckets[label].popleft())
            if buckets[label]: remaining.append(label)
        labels=remaining

    batch_dir=settings.labeling_dir/"manual_auto_172"
    image_dir=batch_dir/"images"; image_dir.mkdir(parents=True,exist_ok=True)
    rows=[]
    for question in selected:
        source=settings.final_dir/question["image_path"]
        destination=image_dir/f"{question['id']}{source.suffix.lower()}"
        shutil.copy2(source,destination)
        objects=[]
        for obj in question["objects"]:
            objects.append({"object_key":str(obj["object_key"]),"label":obj["label"],
                "x":float(obj["bbox_x"]),"y":float(obj["bbox_y"]),"width":float(obj["bbox_width"]),
                "height":float(obj["bbox_height"]),"area_ratio":float(obj["bbox_width"])*float(obj["bbox_height"]),
                "role":obj["role"]})
        targets=[obj for obj in objects if obj["role"]=="target"]
        rows.append({"queue_id":f"manual_{question['id']}","question_id":question["id"],
            "existing_question_id":question["id"],"image_id":question["id"],
            "image_path":str(destination.relative_to(settings.labeling_dir)),
            "question_en":question.get("instruction_en") or f"Manual review: {question['target_label']}",
            "instruction_ko":question["instruction_ko"],"expected_target_count":len(targets),
            "source":question["source"],"split":"manual_auto_review","target_label":question["target_label"],
            "action":"object_drag","qualifier":"","relationship_hints":[],"objects":objects,
            "review_status":"pending","difficulty":question["difficulty"],"translation_status":"manual_review_required"})

    queue=settings.labeling_dir/"queue.jsonl"
    backup=settings.labeling_dir/"queue.before-manual-auto-172.jsonl"
    if queue.exists() and not backup.exists(): shutil.copy2(queue,backup)
    queue.write_text("".join(json.dumps(row,ensure_ascii=False)+"\n" for row in rows))
    all_path=settings.labeling_dir/"relation_candidates_all.jsonl"
    all_rows=[json.loads(line) for line in all_path.read_text().splitlines() if line.strip()]
    selected_ids={row["queue_id"] for row in rows}
    all_rows=[row for row in all_rows if row.get("queue_id") not in selected_ids]+rows
    all_path.write_text("".join(json.dumps(row,ensure_ascii=False)+"\n" for row in all_rows))
    distribution=Counter(row["target_label"] for row in rows)
    (batch_dir/"selection_summary.json").write_text(json.dumps({"selected":len(rows),"distribution":distribution},ensure_ascii=False,indent=2))
    print(json.dumps({"selected":len(rows),"labels":len(distribution),"distribution":distribution},ensure_ascii=False))


if __name__=="__main__": main()
