from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from ultralytics import SAM

from app.config import settings
from app.db import Database


def main() -> None:
    stage = settings.runtime_dir / "masked-pieces-staging"
    report_path = stage / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    items = [item for item in report["items"] if item["method"] == "rectangle"]
    model = SAM("sam2_b.pt")
    database = Database(settings)
    refined = []
    with database.connection(True) as conn, conn.cursor() as cur:
        for item in items:
            cur.execute("SELECT * FROM captcha_questions WHERE id=%s", (item["question_id"],)); question=cur.fetchone()
            cur.execute("SELECT * FROM captcha_objects WHERE question_id=%s AND object_key=%s", (item["question_id"],item["object_key"])); obj=cur.fetchone()
            image=np.array(Image.open(settings.final_dir/question["image_path"]).convert("RGB"));h,w=image.shape[:2]
            box=[max(0,round(obj["bbox_x"]*w)),max(0,round(obj["bbox_y"]*h)),
                 min(w,round((obj["bbox_x"]+obj["bbox_width"])*w)),min(h,round((obj["bbox_y"]+obj["bbox_height"])*h))]
            result=model.predict(image,bboxes=[box],device=0,retina_masks=True,verbose=False)[0]
            mask=np.zeros((h,w),np.uint8)
            if result.masks is not None:
                for polygon_set in result.masks.xy:
                    for polygon in polygon_set if isinstance(polygon_set,list) else [polygon_set]:
                        points=np.asarray(polygon,dtype=np.int32)
                        if len(points)>=3:cv2.fillPoly(mask,[points],1)
            x1,y1,x2,y2=box;mask[:y1]=0;mask[y2:]=0;mask[:,:x1]=0;mask[:,x2:]=0
            ys,xs=np.where(mask>0)
            if len(xs)<64: raise RuntimeError(f"SAM failed for {item['question_id']} {item['object_key']}")
            left,right,top,bottom=max(0,xs.min()-2),min(w,xs.max()+3),max(0,ys.min()-2),min(h,ys.max()+3)
            rgba=np.dstack((image,mask*255))[top:bottom,left:right].astype(np.uint8)
            for root in (stage,settings.final_dir):
                path=root/item["piece_path"];path.parent.mkdir(parents=True,exist_ok=True);Image.fromarray(rgba,"RGBA").save(path,"PNG",optimize=True)
            item["method"]="sam2";item["alpha_pixels"]=int(mask.sum());refined.append(item)
    report["rectangle_failures"]=0
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"sam_refined":len(refined),"items":refined},ensure_ascii=False))


if __name__ == "__main__": main()
