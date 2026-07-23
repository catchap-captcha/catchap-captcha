from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from ultralytics import YOLO

from app.config import settings
from app.db import Database


CLASS_ALIASES = {
    "person": "person", "people": "person", "man": "person", "woman": "person", "boy": "person", "girl": "person",
    "giraffe": "giraffe", "giraffes": "giraffe", "zebra": "zebra", "zebras": "zebra",
    "elephant": "elephant", "elephants": "elephant", "horse": "horse", "horses": "horse",
    "dog": "dog", "dogs": "dog", "cat": "cat", "cats": "cat", "bird": "bird", "birds": "bird",
}


def bbox_pixels(obj: dict, width: int, height: int) -> tuple[int, int, int, int]:
    x1 = max(0, min(width - 1, round(float(obj["bbox_x"]) * width)))
    y1 = max(0, min(height - 1, round(float(obj["bbox_y"]) * height)))
    x2 = max(x1 + 1, min(width, round((float(obj["bbox_x"]) + float(obj["bbox_width"])) * width)))
    y2 = max(y1 + 1, min(height, round((float(obj["bbox_y"]) + float(obj["bbox_height"])) * height)))
    return x1, y1, x2, y2


def iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    x1, y1, x2, y2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union else 0.0


def grabcut(image: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    height, width = image.shape[:2]
    x1, y1, x2, y2 = box
    mask = np.zeros((height, width), np.uint8)
    bgd = np.zeros((1, 65), np.float64); fgd = np.zeros((1, 65), np.float64)
    try:
        cv2.grabCut(image, mask, (x1, y1, max(2, x2-x1), max(2, y2-y1)), bgd, fgd, 5, cv2.GC_INIT_WITH_RECT)
        result = np.isin(mask, (cv2.GC_FGD, cv2.GC_PR_FGD))
    except cv2.error:
        result = np.zeros((height, width), bool)
    result[:y1] = False; result[y2:] = False; result[:, :x1] = False; result[:, x2:] = False
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="yolo11l-seg.pt")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    database = Database(settings)
    with database.connection(True) as conn, conn.cursor() as cur:
        cur.execute("""SELECT * FROM captcha_questions WHERE source='tallyqa_visual_genome'
                       AND status='active' AND review_status='approved' ORDER BY id""")
        questions = cur.fetchall()
        if args.limit: questions = questions[:args.limit]
        for question in questions:
            cur.execute("SELECT * FROM captcha_objects WHERE question_id=%s AND role IN ('target','decoy') ORDER BY id", (question["id"],))
            question["objects"] = cur.fetchall()

    model = YOLO(args.model)
    stage = settings.runtime_dir / "masked-pieces-staging"
    if stage.exists(): shutil.rmtree(stage)
    stage.mkdir(parents=True)
    report = []
    total_objects = fallbacks = failures = overlaps_before = 0
    for number, question in enumerate(questions, 1):
        source = settings.final_dir / question["image_path"]
        rgb = np.array(Image.open(source).convert("RGB")); height, width = rgb.shape[:2]
        result = model.predict(rgb, device=0, imgsz=1024, conf=.18, iou=.55, retina_masks=True, verbose=False)[0]
        detections = []
        if result.boxes is not None and result.masks is not None:
            for idx, polygon_set in enumerate(result.masks.xy):
                canvas = np.zeros((height, width), np.uint8)
                for polygon in polygon_set if isinstance(polygon_set, list) else [polygon_set]:
                    points = np.asarray(polygon, dtype=np.int32)
                    if len(points) >= 3: cv2.fillPoly(canvas, [points], 1)
                box = tuple(int(value) for value in result.boxes.xyxy[idx].detach().cpu().tolist())
                cls = result.names[int(result.boxes.cls[idx].item())]
                detections.append({"mask": canvas.astype(bool), "box": box, "class": cls,
                                   "confidence": float(result.boxes.conf[idx].item())})
        object_boxes = [bbox_pixels(obj, width, height) for obj in question["objects"]]
        pairs = []
        for oi, (obj, box) in enumerate(zip(question["objects"], object_boxes)):
            expected = CLASS_ALIASES.get(str(obj["label"]).lower())
            for di, det in enumerate(detections):
                if expected and det["class"] != expected: continue
                score = iou(box, det["box"])
                cx=(det["box"][0]+det["box"][2])/2; cy=(det["box"][1]+det["box"][3])/2
                if box[0] <= cx <= box[2] and box[1] <= cy <= box[3]: score += .35
                score += det["confidence"] * .1
                if score >= .12: pairs.append((score, oi, di))
        assignment: dict[int, int] = {}; used = set()
        for _, oi, di in sorted(pairs, reverse=True):
            if oi not in assignment and di not in used: assignment[oi] = di; used.add(di)
        masks = []
        methods = []
        for oi, box in enumerate(object_boxes):
            if oi in assignment:
                mask = detections[assignment[oi]]["mask"].copy(); method = "yolo"
                x1,y1,x2,y2=box; mask[:y1]=False;mask[y2:]=False;mask[:,:x1]=False;mask[:,x2:]=False
            else:
                mask = grabcut(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), box); method = "grabcut"; fallbacks += 1
            if mask.sum() < 64:
                x1,y1,x2,y2=box; mask=np.zeros((height,width),bool);mask[y1:y2,x1:x2]=True;method="rectangle";failures += 1
            masks.append(mask); methods.append(method)
        stack = np.stack(masks)
        overlap = stack.sum(axis=0) > 1
        overlaps_before += int(overlap.sum())
        if overlap.any():
            yy, xx = np.indices((height, width)); distances=[]
            for box in object_boxes:
                cx=(box[0]+box[2])/2;cy=(box[1]+box[3])/2
                distances.append(((xx-cx)/max(1,box[2]-box[0]))**2+((yy-cy)/max(1,box[3]-box[1]))**2)
            distances=np.stack(distances); distances[~stack]=np.inf
            owner=np.argmin(distances,axis=0)
            for oi in range(len(masks)): masks[oi][overlap] = owner[overlap] == oi
        for obj, mask, method in zip(question["objects"], masks, methods):
            ys,xs=np.where(mask)
            if not len(xs): continue
            x1,x2,y1,y2=max(0,xs.min()-2),min(width,xs.max()+3),max(0,ys.min()-2),min(height,ys.max()+3)
            rgba=np.dstack((rgb, (mask*255).astype(np.uint8)))[y1:y2,x1:x2]
            destination=stage / obj["piece_path"];destination.parent.mkdir(parents=True,exist_ok=True)
            Image.fromarray(rgba,"RGBA").save(destination,"PNG",optimize=True)
            total_objects += 1
            report.append({"question_id":question["id"],"object_key":obj["object_key"],"method":method,
                           "piece_path":obj["piece_path"],"alpha_pixels":int(mask.sum())})
        if number % 20 == 0: print(json.dumps({"processed_questions":number,"objects":total_objects,"fallbacks":fallbacks}))
    expected=sum(len(q["objects"]) for q in questions)
    complete=total_objects==expected
    (stage/"report.json").write_text(json.dumps({"questions":len(questions),"expected_objects":expected,
        "generated_objects":total_objects,"fallbacks":fallbacks,"rectangle_failures":failures,
        "overlap_pixels_resolved":overlaps_before,"complete":complete,"items":report},ensure_ascii=False,indent=2))
    if args.apply:
        if not complete: raise RuntimeError("Staging validation failed; not applying")
        backup=settings.final_dir/"pieces.before-instance-masks"
        if not backup.exists(): shutil.copytree(settings.final_dir/"pieces",backup)
        for item in report:
            src=stage/item["piece_path"];dst=settings.final_dir/item["piece_path"]
            dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
    print(json.dumps({"questions":len(questions),"expected_objects":expected,"generated_objects":total_objects,
        "fallbacks":fallbacks,"rectangle_failures":failures,"overlap_pixels_resolved":overlaps_before,
        "complete":complete,"applied":args.apply},ensure_ascii=False))


if __name__ == "__main__": main()
