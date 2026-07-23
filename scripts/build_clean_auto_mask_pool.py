from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from ultralytics import YOLO

from app.config import settings
from app.db import Database, utcnow


SOURCES = ("train2014", "val2014", "VG_100K", "VG_100K_2")
ALIASES = {
    "man":"person", "woman":"person", "boy":"person", "girl":"person", "people":"person",
    "bike":"bicycle", "sofa":"couch", "table":"dining table", "plant":"potted plant",
    "phone":"cell phone", "television":"tv", "ball":"sports ball",
}


def canonical(label: str) -> str:
    return ALIASES.get(label.strip().lower(), label.strip().lower())


def box_pixels(obj: dict, width: int, height: int) -> tuple[int, int, int, int]:
    x1=max(0,min(width-1,round(float(obj["bbox_x"])*width)))
    y1=max(0,min(height-1,round(float(obj["bbox_y"])*height)))
    x2=max(x1+1,min(width,round((float(obj["bbox_x"])+float(obj["bbox_width"]))*width)))
    y2=max(y1+1,min(height,round((float(obj["bbox_y"])+float(obj["bbox_height"]))*height)))
    return x1,y1,x2,y2


def intersection(a, b) -> int:
    return max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))


def iou(a, b) -> float:
    inter=intersection(a,b); union=(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-inter
    return inter/union if union else 0


def load_questions(database: Database, limit: int | None) -> list[dict]:
    marks=",".join(["%s"]*len(SOURCES))
    with database.connection(True) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT * FROM captcha_questions WHERE id LIKE 'auto\\_%%' AND source IN ({marks}) ORDER BY id", SOURCES)
        questions=cur.fetchall()
        if limit: questions=questions[:limit]
        for question in questions:
            cur.execute("SELECT * FROM captcha_objects WHERE question_id=%s AND role IN ('target','decoy') ORDER BY id",(question["id"],))
            question["objects"]=cur.fetchall()
    return questions


def detections(result, width: int, height: int) -> list[dict]:
    output=[]
    if result.boxes is None or result.masks is None: return output
    for idx, polygon_set in enumerate(result.masks.xy):
        canvas=np.zeros((height,width),np.uint8)
        polygons=polygon_set if isinstance(polygon_set,list) else [polygon_set]
        for polygon in polygons:
            points=np.asarray(polygon,dtype=np.int32)
            if len(points)>=3: cv2.fillPoly(canvas,[points],1)
        output.append({
            "mask":canvas.astype(bool),
            "box":tuple(int(v) for v in result.boxes.xyxy[idx].detach().cpu().tolist()),
            "class":result.names[int(result.boxes.cls[idx].item())],
            "confidence":float(result.boxes.conf[idx].item()),
        })
    return output


def match_objects(objects: list[dict], boxes: list[tuple], detected: list[dict], names: set[str]):
    pairs=[]
    for oi,(obj,box) in enumerate(zip(objects,boxes)):
        expected=canonical(str(obj["label"]))
        if expected not in names: return None, f"unsupported_class:{expected}"
        for di,det in enumerate(detected):
            if det["class"]!=expected or det["confidence"]<.30: continue
            inter=intersection(box,det["box"])
            det_area=max(1,(det["box"][2]-det["box"][0])*(det["box"][3]-det["box"][1]))
            containment=inter/det_area
            cx=(det["box"][0]+det["box"][2])/2; cy=(det["box"][1]+det["box"][3])/2
            centered=box[0]<=cx<=box[2] and box[1]<=cy<=box[3]
            score=iou(box,det["box"])+.45*containment+(.25 if centered else 0)+.08*det["confidence"]
            if score>=.48 and containment>=.35: pairs.append((score,oi,di))
    assignment={}; used=set()
    for score,oi,di in sorted(pairs,reverse=True):
        if oi not in assignment and di not in used: assignment[oi]=(di,score); used.add(di)
    if len(assignment)!=len(objects): return None,"unmatched_object"
    return assignment,None


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--model",default="yolo11l-seg.pt")
    parser.add_argument("--limit",type=int)
    parser.add_argument("--apply",action="store_true")
    parser.add_argument("--resume",action="store_true")
    args=parser.parse_args()
    database=Database(settings); questions=load_questions(database,args.limit)
    model=YOLO(args.model); names=set(model.names.values())
    stage=settings.runtime_dir/"clean-auto-mask-staging"
    if stage.exists() and not args.resume: shutil.rmtree(stage)
    (stage/"pieces").mkdir(parents=True,exist_ok=True)
    accepted_path=stage/"accepted.jsonl"; rejected_path=stage/"rejected.jsonl"
    completed=set()
    if args.resume:
        for path in (accepted_path,rejected_path):
            if path.exists():
                completed.update(json.loads(line)["question_id"] for line in path.read_text().splitlines() if line.strip())
    counters=Counter(); accepted=[]
    if accepted_path.exists(): accepted=[json.loads(line) for line in accepted_path.read_text().splitlines() if line.strip()]
    for number,question in enumerate(questions,1):
        if question["id"] in completed: continue
        source=settings.final_dir/question["image_path"]
        reason=None; metrics={}
        try:
            rgb=np.array(Image.open(source).convert("RGB")); height,width=rgb.shape[:2]
            result=model.predict(rgb,device=0,imgsz=1024,conf=.25,iou=.55,retina_masks=True,verbose=False)[0]
            found=detections(result,width,height); boxes=[box_pixels(obj,width,height) for obj in question["objects"]]
            assignment,reason=match_objects(question["objects"],boxes,found,names)
            masks=[]; object_metrics=[]
            if assignment:
                for oi,(obj,box) in enumerate(zip(question["objects"],boxes)):
                    di,score=assignment[oi]; mask=found[di]["mask"].copy()
                    x1,y1,x2,y2=box; mask[:y1]=False;mask[y2:]=False;mask[:,:x1]=False;mask[:,x2:]=False
                    pixels=int(mask.sum()); box_area=(x2-x1)*(y2-y1); fill=pixels/max(1,box_area)
                    if pixels<225 or not .035<=fill<=.94:
                        reason="poor_mask_area"; break
                    masks.append(mask); object_metrics.append({"object_key":obj["object_key"],"confidence":found[di]["confidence"],"match_score":score,"fill_ratio":fill,"alpha_pixels":pixels})
            if not reason and masks:
                stack=np.stack(masks); overlap_pixels=int((stack.sum(axis=0)>1).sum()); union_pixels=int((stack.sum(axis=0)>0).sum())
                overlap_ratio=overlap_pixels/max(1,union_pixels); metrics["overlap_ratio"]=overlap_ratio
                if overlap_ratio>.025: reason="excessive_overlap"
                elif overlap_pixels:
                    yy,xx=np.indices((height,width)); distances=[]
                    for box in boxes:
                        cx=(box[0]+box[2])/2;cy=(box[1]+box[3])/2
                        distances.append(((xx-cx)/max(1,box[2]-box[0]))**2+((yy-cy)/max(1,box[3]-box[1]))**2)
                    distances=np.stack(distances);distances[~stack]=np.inf;owner=np.argmin(distances,axis=0); overlap=stack.sum(axis=0)>1
                    for oi in range(len(masks)): masks[oi][overlap]=owner[overlap]==oi
            if not reason and masks:
                for obj,mask in zip(question["objects"],masks):
                    ys,xs=np.where(mask)
                    if not len(xs): reason="empty_after_overlap"; break
                    x1,x2=max(0,xs.min()-2),min(width,xs.max()+3);y1,y2=max(0,ys.min()-2),min(height,ys.max()+3)
                    rgba=np.dstack((rgb,(mask*255).astype(np.uint8)))[y1:y2,x1:x2]
                    dest=stage/obj["piece_path"];dest.parent.mkdir(parents=True,exist_ok=True)
                    Image.fromarray(rgba,"RGBA").save(dest,"PNG",optimize=True)
        except Exception as exc:
            reason=f"error:{type(exc).__name__}"
        if reason:
            counters[reason]+=1
            with rejected_path.open("a") as out: out.write(json.dumps({"question_id":question["id"],"reason":reason},ensure_ascii=False)+"\n")
            for obj in question["objects"]:
                staged=stage/obj["piece_path"]
                if staged.exists(): staged.unlink()
        else:
            row={"question_id":question["id"],"objects":object_metrics,**metrics};accepted.append(row);counters["accepted"]+=1
            with accepted_path.open("a") as out: out.write(json.dumps(row,ensure_ascii=False)+"\n")
        if number%25==0 or number==len(questions):
            print(json.dumps({"processed":number,"total":len(questions),"accepted":len(accepted),"rejected":number-len(accepted)},ensure_ascii=False),flush=True)
    accepted_ids={row["question_id"] for row in accepted}
    if args.apply:
        for question in questions:
            if question["id"] not in accepted_ids: continue
            for obj in question["objects"]:
                src=stage/obj["piece_path"];dst=settings.final_dir/obj["piece_path"]
                dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
        now=utcnow(); marks=",".join(["%s"]*len(SOURCES))
        with database.connection() as conn,conn.cursor() as cur:
            cur.execute(f"UPDATE captcha_questions SET status='inactive' WHERE id LIKE 'auto\\_%%' AND source IN ({marks})",SOURCES)
            if accepted_ids:
                ids=sorted(accepted_ids)
                for offset in range(0,len(ids),500):
                    chunk=ids[offset:offset+500]; qs=",".join(["%s"]*len(chunk))
                    cur.execute(f"UPDATE captcha_questions SET status='active',review_status='approved',reviewer='auto-instance-mask-qc',reviewed_at=%s WHERE id IN ({qs})",[now,*chunk])
            conn.commit()
        by_id={q["id"]:q for q in questions}
        manifest=settings.final_dir/"challenges.jsonl"
        preserved=[]
        if manifest.exists(): preserved=[json.loads(line) for line in manifest.read_text().splitlines() if line.strip() and json.loads(line).get("challenge_id") not in by_id]
        generated=[]
        for qid in sorted(accepted_ids):
            q=by_id[qid]
            generated.append({"challenge_id":qid,"source":q["source"],"source_question_id":q["source_question_id"],"image_path":q["image_path"],"instruction":q["instruction_ko"],"difficulty":q["difficulty"],"review_status":"approved","objects":[{"object_key":o["object_key"],"label":o["label"],"bbox":[float(o["bbox_x"]),float(o["bbox_y"]),float(o["bbox_width"]),float(o["bbox_height"])],"role":o["role"],"piece_path":o["piece_path"]} for o in q["objects"]]})
        backup=settings.final_dir/"challenges.before-clean-auto.jsonl"
        if manifest.exists() and not backup.exists(): shutil.copy2(manifest,backup)
        manifest.write_text("".join(json.dumps(row,ensure_ascii=False)+"\n" for row in preserved+generated))
        (settings.final_dir/"clean_auto_challenges.jsonl").write_text("".join(json.dumps(row,ensure_ascii=False)+"\n" for row in generated))
    report={"scanned":len(questions),"accepted":len(accepted_ids),"rejected":len(questions)-len(accepted_ids),"reasons":dict(counters),"applied":args.apply}
    (stage/"summary.json").write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=="__main__": main()
