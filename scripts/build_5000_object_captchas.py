from __future__ import annotations

import argparse
import json
import os
import shutil
import zipfile
from collections import defaultdict
from pathlib import Path

from PIL import Image

from app.config import ROOT_DIR, settings
from app.db import Database, utcnow


LABELS = {
    "person":"사람","man":"남자","woman":"여자","boy":"남자아이","girl":"여자아이",
    "bicycle":"자전거","bike":"자전거","car":"자동차","bus":"버스","truck":"트럭","motorcycle":"오토바이",
    "dog":"개","cat":"고양이","horse":"말","cow":"소","sheep":"양","giraffe":"기린","zebra":"얼룩말",
    "elephant":"코끼리","bear":"곰","bird":"새","animal":"동물","bench":"벤치","chair":"의자","table":"테이블",
    "bottle":"병","cup":"컵","umbrella":"우산","backpack":"가방","handbag":"가방","suitcase":"여행가방",
    "book":"책","clock":"시계","vase":"꽃병","cell phone":"휴대전화","phone":"휴대전화","laptop":"노트북",
    "tv":"텔레비전","television":"텔레비전","skateboard":"스케이트보드","surfboard":"서핑보드","boat":"보트",
    "airplane":"비행기","train":"기차","traffic light":"신호등","fire hydrant":"소화전","stop sign":"정지표지판",
    "potted plant":"화분","plant":"식물","toilet":"변기","bed":"침대","couch":"소파","sofa":"소파",
    "refrigerator":"냉장고","oven":"오븐","sink":"싱크대","toaster":"토스터","microwave":"전자레인지",
    "fork":"포크","knife":"칼","spoon":"숟가락","bowl":"그릇","banana":"바나나","apple":"사과",
    "orange":"오렌지","sandwich":"샌드위치","pizza":"피자","donut":"도넛","cake":"케이크","kite":"연",
    "baseball bat":"야구방망이","baseball glove":"야구글러브","sports ball":"공","ball":"공","frisbee":"원반",
    "skis":"스키","snowboard":"스노보드","tie":"넥타이","scissors":"가위","teddy bear":"곰인형",
    "hair drier":"헤어드라이어","toothbrush":"칫솔","helmet":"헬멧","hat":"모자","shoe":"신발",
}


def read_zip_json(path: Path, suffix: str):
    with zipfile.ZipFile(path) as archive:
        member=next(name for name in archive.namelist() if name.endswith(suffix))
        with archive.open(member) as source: return json.load(source)


def canonical(raw: str) -> str | None:
    name=raw.strip().lower().replace("_"," ")
    if name.endswith("s") and name[:-1] in LABELS: name=name[:-1]
    return name if name in LABELS else None


def transform(box: list[float], original_width: int, original_height: int) -> dict | None:
    scale=max(640/original_width,360/original_height)
    crop_x=(original_width*scale-640)/2; crop_y=(original_height*scale-360)/2
    x=box[0]*scale-crop_x; y=box[1]*scale-crop_y; w=box[2]*scale; h=box[3]*scale
    left=max(0,x); top=max(0,y); right=min(640,x+w); bottom=min(360,y+h)
    w=right-left; h=bottom-top
    if w<24 or h<24 or w*h<2300 or w*h>640*360*.65: return None
    return {"x":left/640,"y":top/360,"width":w/640,"height":h/360}


def overlap(a: dict,b: dict) -> float:
    left=max(a["x"],b["x"]); top=max(a["y"],b["y"])
    right=min(a["x"]+a["width"],b["x"]+b["width"]); bottom=min(a["y"]+a["height"],b["y"]+b["height"])
    inter=max(0,right-left)*max(0,bottom-top)
    union=a["width"]*a["height"]+b["width"]*b["height"]-inter
    return inter/union if union else 0


def load_annotations(root: Path, sources: set[str]) -> dict[str,list[dict]]:
    result: dict[str,list[dict]]=defaultdict(list)
    coco_zip=root/"data/annotations/coco2014/annotations_trainval2014.zip"
    source_by_file={Path(source).name:source for source in sources}
    wanted_filenames=set(source_by_file)
    for member in ("instances_train2014.json","instances_val2014.json"):
        data=read_zip_json(coco_zip,member); categories={row["id"]:canonical(row["name"]) for row in data["categories"]}
        images={row["id"]:row for row in data["images"] if row["file_name"] in wanted_filenames}
        for ann in data["annotations"]:
            image=images.get(ann["image_id"]); label=categories.get(ann["category_id"])
            if not image or not label: continue
            source=source_by_file.get(image["file_name"]); box=transform(ann["bbox"],image["width"],image["height"])
            if source and box: result[source].append({"key":f"coco_{ann['id']}","label":label,**box})
    vg_sources={source for source in sources if source.startswith("VG_")}; vg_ids={int(Path(source).stem) for source in vg_sources}
    image_rows=read_zip_json(root/"data/annotations/visual_genome/image_data.json.zip","image_data.json")
    sizes={int(row["image_id"]):(int(row["width"]),int(row["height"])) for row in image_rows if int(row["image_id"]) in vg_ids}
    objects=read_zip_json(root/"data/annotations/visual_genome/objects.json.zip","objects.json")
    source_by_id={int(Path(source).stem):source for source in vg_sources}
    for row in objects:
        image_id=int(row["image_id"])
        if image_id not in vg_ids or image_id not in sizes: continue
        width,height=sizes[image_id]; source=source_by_id[image_id]
        for obj in row.get("objects",[]):
            names=obj.get("names") or [obj.get("name","")]; label=next((canonical(str(name)) for name in names if canonical(str(name))),None)
            if not label: continue
            box=transform([obj["x"],obj["y"],obj.get("w",obj.get("width",0)),obj.get("h",obj.get("height",0))],width,height)
            if box: result[source].append({"key":f"vg_{obj['object_id']}","label":label,**box})
    return result


def dedupe(objects: list[dict]) -> list[dict]:
    kept=[]
    for obj in sorted(objects,key=lambda row:row["width"]*row["height"],reverse=True):
        if any(obj["label"]==other["label"] and overlap(obj,other)>.72 for other in kept): continue
        kept.append(obj)
    return kept


def choose(objects: list[dict]) -> tuple[dict,list[dict],str] | None:
    if len(objects)<2: return None
    groups: dict[str,list[dict]]=defaultdict(list)
    for obj in objects: groups[obj["label"]].append(obj)
    labels=sorted(groups,key=lambda label:(len(groups[label])!=1,-max(o["width"]*o["height"] for o in groups[label])))
    label=labels[0]; group=groups[label]
    if len(group)==1:
        target=group[0]; instruction=f"사진 속 {LABELS[label]}을 정답존으로 옮기세요."
    else:
        target=min(group,key=lambda obj:obj["x"]+obj["width"]/2); instruction=f"사진에서 가장 왼쪽에 있는 {LABELS[label]}을 정답존으로 옮기세요."
    decoys=[obj for obj in objects if obj["key"]!=target["key"]]
    decoys=sorted(decoys,key=lambda obj:obj["width"]*obj["height"],reverse=True)[:7]
    return target,decoys,instruction


def link_or_copy(source: Path,destination: Path) -> None:
    destination.parent.mkdir(parents=True,exist_ok=True)
    if destination.exists(): return
    try: os.link(source,destination)
    except OSError: shutil.copy2(source,destination)


def main() -> None:
    parser=argparse.ArgumentParser(); parser.add_argument("--limit",type=int,default=5000); args=parser.parse_args()
    manifest=[json.loads(line) for line in (ROOT_DIR/"data/processed/manifest.jsonl").read_text().splitlines() if line.strip()]
    annotations=load_annotations(ROOT_DIR,{row["source"] for row in manifest}); database=Database(settings); database.initialize()
    with database.connection(True) as conn,conn.cursor() as cur:
        cur.execute("SELECT id FROM captcha_questions WHERE id LIKE 'auto\\_%'")
        existing={row["id"] for row in cur.fetchall()}
    generated=0; object_count=0; now=utcnow()
    for row in manifest:
        objects=dedupe(annotations.get(row["source"],[])); selected=choose(objects)
        if not selected: continue
        target,decoys,instruction=selected; question_id=f"auto_{generated+1:05d}"
        if question_id in existing:
            generated += 1
            if generated>=args.limit: break
            continue
        image_rel=f"images/{question_id}.jpg"; source_image=ROOT_DIR/"data/processed"/row["file"]
        link_or_copy(source_image,settings.final_dir/image_rel)
        final_objects=[]
        for index,(obj,role) in enumerate([(target,"target")]+[(decoy,"decoy") for decoy in decoys],1):
            piece_rel=f"pieces/{question_id}-{index}.png"; piece=settings.final_dir/piece_rel
            x=round(obj["x"]*640); y=round(obj["y"]*360); right=round((obj["x"]+obj["width"])*640); bottom=round((obj["y"]+obj["height"])*360)
            with Image.open(source_image) as image: image.crop((x,y,right,bottom)).convert("RGBA").save(piece,"PNG",optimize=True)
            final_objects.append({"object_key":obj["key"],"label":obj["label"],"x":obj["x"],"y":obj["y"],
                                  "width":obj["width"],"height":obj["height"],"role":role,"piece_path":piece_rel})
        database.upsert_question({"id":question_id,"type":"object_drag","instruction_ko":instruction,"instruction_en":None,
            "source":row["source"].split("/",1)[0],"source_question_id":row["source"],"image_path":image_rel,
            "image_width":640,"image_height":360,"difficulty":2 if len(decoys)<4 else 3,"status":"active",
            "review_status":"approved","reviewer":"official-object-annotations","reviewed_at":now,"created_at":now},final_objects)
        generated+=1; object_count+=len(final_objects)
        if generated%100==0: print(f"generated {generated}/{args.limit}",flush=True)
        if generated>=args.limit: break
    with database.connection() as conn,conn.cursor() as cur:
        cur.execute("UPDATE captcha_questions SET status='inactive' WHERE id NOT LIKE 'auto\\_%'")
        conn.commit()
    print(json.dumps({"generated":generated,"objects":object_count,"skipped":len(manifest)-generated},ensure_ascii=False))


if __name__=="__main__": main()
