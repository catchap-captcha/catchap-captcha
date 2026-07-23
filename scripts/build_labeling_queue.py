from __future__ import annotations

import argparse
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps


ARCHIVES = {"VG_100K":"images.zip","VG_100K_2":"images2.zip"}
DRINK_PATTERNS = ("drinking", "drink from", "drinks from", "taking a drink", "head near water", "head in water")


def complex_value(value: Any) -> bool:
    if isinstance(value,bool): return not value
    if isinstance(value,str): return value.strip().lower() in {"false","0","complex"}
    return value == 0


def image_reference(row: dict) -> str:
    value=str(row.get("image") or row.get("image_path") or "")
    if value.startswith(("VG_100K/","VG_100K_2/")): return value
    image_id=row.get("image_id")
    return f"VG_100K/{image_id}.jpg" if image_id else value


def filter_candidates(metadata: Path) -> list[dict]:
    rows=[]
    for split in ("train","test"):
        for row in json.loads((metadata/f"{split}.json").read_text(encoding="utf-8")):
            question=str(row.get("question","")).lower()
            try: answer=int(row.get("answer"))
            except (TypeError,ValueError): continue
            image=image_reference(row)
            if complex_value(row.get("issimple")) and 1<=answer<=4 and "giraffe" in question and "drink" in question and image.startswith("VG_100K"):
                rows.append({**row,"answer":answer,"image":image,"split":split})
    return rows


def read_zip_json(path: Path) -> list[dict]:
    with zipfile.ZipFile(path) as archive:
        name=next(name for name in archive.namelist() if name.endswith(".json"))
        with archive.open(name) as source: return json.load(io.TextIOWrapper(source,encoding="utf-8"))


def names(obj: dict) -> list[str]:
    raw=obj.get("names") or [obj.get("name","")]
    return [str(value).strip().lower() for value in raw if value]


def object_box(obj: dict, width: int, height: int) -> dict | None:
    x=float(obj.get("x",0)); y=float(obj.get("y",0)); w=float(obj.get("w",obj.get("width",0))); h=float(obj.get("h",obj.get("height",0)))
    if min(w,h)<=0 or x<0 or y<0 or x+w>width+2 or y+h>height+2: return None
    return {"object_key":str(obj.get("object_id")),"label":names(obj)[0] if names(obj) else "giraffe",
            "x":x/width,"y":y/height,"width":w/width,"height":h/height,"area_ratio":w*h/(width*height)}


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",type=Path,default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--limit",type=int,default=100)
    args=parser.parse_args(); root=args.root
    metadata=root/"data/metadata"; raw=root/"data/raw"; vg=root/"data/annotations/visual_genome"; labeling=root/"data/labeling"
    images_dir=labeling/"images"; images_dir.mkdir(parents=True,exist_ok=True)
    candidates=filter_candidates(metadata)
    ids={int(Path(row["image"]).stem) for row in candidates}
    object_data={int(row["image_id"]):row.get("objects",[]) for row in read_zip_json(vg/"objects.json.zip") if int(row["image_id"]) in ids}
    relation_data={int(row["image_id"]):row.get("relationships",[]) for row in read_zip_json(vg/"relationships.json.zip") if int(row["image_id"]) in ids}
    image_data={int(row["image_id"]):row for row in read_zip_json(vg/"image_data.json.zip") if int(row["image_id"]) in ids}
    queues=[]; rejected=[]
    archives={name:zipfile.ZipFile(raw/file) for name,file in ARCHIVES.items()}
    try:
        for candidate in candidates:
            image_id=int(Path(candidate["image"]).stem); meta=image_data.get(image_id,{})
            width=int(meta.get("width",0)); height=int(meta.get("height",0))
            objects=[]
            for obj in object_data.get(image_id,[]):
                obj_names=names(obj)
                if not any(name in {"giraffe","giraffes"} or name.endswith(" giraffe") for name in obj_names): continue
                box=object_box(obj,width,height) if width and height else None
                if box and box["area_ratio"]>=0.01: objects.append(box)
            reason=None
            if not objects: reason="no_usable_giraffe_bbox"
            elif len(objects)<candidate["answer"]: reason="bbox_count_below_answer"
            elif not 2<=len(objects)<=6: reason="giraffe_count_out_of_range"
            if reason:
                rejected.append({"question_id":candidate.get("question_id"),"image":candidate["image"],"reason":reason}); continue
            root_name=candidate["image"].split("/",1)[0]
            try: payload=archives[root_name].read(candidate["image"])
            except KeyError:
                rejected.append({"question_id":candidate.get("question_id"),"image":candidate["image"],"reason":"image_missing"}); continue
            output_name=f"{image_id}.jpg"; output=images_dir/output_name
            with Image.open(io.BytesIO(payload)) as image: ImageOps.exif_transpose(image).convert("RGB").save(output,"JPEG",quality=94,optimize=True)
            related=[]
            object_ids={box["object_key"] for box in objects}
            for relation in relation_data.get(image_id,[]):
                predicate=str(relation.get("predicate","")).lower()
                subject=relation.get("subject",{})
                if str(subject.get("object_id")) in object_ids and any(term in predicate for term in DRINK_PATTERNS):
                    related.append({"subject_id":subject.get("object_id"),"predicate":predicate,"object":names(relation.get("object",{}))})
            queues.append({"queue_id":f"tallyqa_{candidate.get('question_id',image_id)}","question_id":candidate.get("question_id",image_id),
                "image_id":image_id,"image_path":f"images/{output_name}","question_en":candidate["question"],
                "instruction_ko":"물을 마시고 있는 기린을 모두 정답존으로 옮기세요.","expected_target_count":candidate["answer"],
                "source":"visual_genome","split":candidate["split"],"relationship_hints":related,
                "objects":[{**box,"role":"ambiguous"} for box in objects],"review_status":"pending"})
            if len(queues)>=args.limit: break
    finally:
        for archive in archives.values(): archive.close()
    (labeling/"queue.jsonl").write_text("".join(json.dumps(row,ensure_ascii=False)+"\n" for row in queues),encoding="utf-8")
    (labeling/"rejected.jsonl").write_text("".join(json.dumps(row,ensure_ascii=False)+"\n" for row in rejected),encoding="utf-8")
    (labeling/"candidate_image_ids.txt").write_text("\n".join(str(row["image_id"]) for row in queues)+"\n",encoding="utf-8")
    print(json.dumps({"candidates":len(candidates),"queued":len(queues),"rejected":len(rejected)},ensure_ascii=False))


if __name__=="__main__": main()
