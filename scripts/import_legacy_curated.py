from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

from PIL import Image

from app.config import ROOT_DIR, settings
from app.db import Database, utcnow


def zip_json(path: Path, member_suffix: str) -> dict | list:
    with zipfile.ZipFile(path) as archive:
        member = next(name for name in archive.namelist() if name.endswith(member_suffix))
        with archive.open(member) as source:
            return json.load(source)


def intersects_target(box: dict, targets: list[dict]) -> bool:
    for target in targets:
        left=max(box["x"],target["x"]); top=max(box["y"],target["y"])
        right=min(box["x"]+box["width"],target["x"]+target["width"])
        bottom=min(box["y"]+box["height"],target["y"]+target["height"])
        intersection=max(0,right-left)*max(0,bottom-top)
        smaller=min(box["width"]*box["height"],target["width"]*target["height"])
        if smaller and intersection/smaller >= 0.15: return True
    return False


def scale_box(box: list[float], item: dict) -> dict:
    original=item["original_size"]; content=item["content_rect"]
    scale_x=content["width"]/original["width"]; scale_y=content["height"]/original["height"]
    return {"x":round(content["x"]+box[0]*scale_x),"y":round(content["y"]+box[1]*scale_y),
            "width":round(box[2]*scale_x),"height":round(box[3]*scale_y)}


def annotation_boxes(manifest: list[dict]) -> dict[str,list[dict]]:
    result={item["source"]:[] for item in manifest}
    vg_ids={int(Path(item["source"]).stem) for item in manifest if item["source"].startswith("VG_")}
    vg_rows=zip_json(ROOT_DIR/"data/annotations/visual_genome/objects.json.zip","objects.json")
    by_vg={int(row["image_id"]):row.get("objects",[]) for row in vg_rows if int(row["image_id"]) in vg_ids}
    for item in manifest:
        if not item["source"].startswith("VG_"): continue
        for obj in by_vg.get(int(Path(item["source"]).stem),[]):
            names=[str(name).lower() for name in (obj.get("names") or [obj.get("name","")])]
            if not any(name in {"giraffe","giraffes"} for name in names): continue
            result[item["source"]].append(scale_box([obj["x"],obj["y"],obj.get("w",obj.get("width")),obj.get("h",obj.get("height"))],item))
    coco=zip_json(ROOT_DIR/"data/annotations/coco2014/annotations_trainval2014.zip","instances_train2014.json")
    coco_val=zip_json(ROOT_DIR/"data/annotations/coco2014/annotations_trainval2014.zip","instances_val2014.json")
    for dataset in (coco,coco_val):
        giraffe_ids={cat["id"] for cat in dataset["categories"] if cat["name"]=="giraffe"}
        images={row["id"]:row["file_name"] for row in dataset["images"]}
        wanted={Path(item["source"]).name:item for item in manifest}
        for ann in dataset["annotations"]:
            filename=images.get(ann["image_id"])
            if ann["category_id"] not in giraffe_ids or filename not in wanted: continue
            item=wanted[filename]; result[item["source"]].append(scale_box(ann["bbox"],item))
    return result


def main() -> None:
    source_dir = ROOT_DIR / "data/giraffe_drinking"
    manifest = json.loads((source_dir / "manifest.json").read_text(encoding="utf-8"))
    candidates = annotation_boxes(manifest)
    database = Database(settings); database.initialize(); imported = 0; total_decoys = 0
    (settings.final_dir / "images").mkdir(parents=True, exist_ok=True)
    (settings.final_dir / "pieces").mkdir(parents=True, exist_ok=True)
    for index, item in enumerate(manifest, 1):
        source = source_dir / item["file"]
        if not source.exists() or not item.get("targets"): continue
        question_id = f"legacy_gd_{index:03d}"
        final_image = settings.final_dir / "images" / f"{question_id}.jpg"
        final_image.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, final_image)
        with Image.open(final_image) as image: width, height = image.size
        objects=[]
        for object_index, box in enumerate(item["targets"], 1):
            key=f"target_{object_index}"; piece_rel=f"pieces/{question_id}-{key}.png"
            with Image.open(final_image) as image:
                image.crop((box["x"],box["y"],box["x"]+box["width"],box["y"]+box["height"])).convert("RGBA").save(settings.final_dir/piece_rel,"PNG",optimize=True)
            objects.append({"object_key":key,"label":"giraffe","x":box["x"]/width,"y":box["y"]/height,
                            "width":box["width"]/width,"height":box["height"]/height,"role":"target","piece_path":piece_rel})
        decoy_index=0
        for box in candidates.get(item["source"],[]):
            if intersects_target(box,item["targets"]): continue
            if box["width"]<18 or box["height"]<18: continue
            decoy_index += 1; key=f"decoy_{decoy_index}"; piece_rel=f"pieces/{question_id}-{key}.png"
            with Image.open(final_image) as image:
                image.crop((box["x"],box["y"],box["x"]+box["width"],box["y"]+box["height"])).convert("RGBA").save(settings.final_dir/piece_rel,"PNG",optimize=True)
            objects.append({"object_key":key,"label":"giraffe","x":box["x"]/width,"y":box["y"]/height,
                            "width":box["width"]/width,"height":box["height"]/height,"role":"decoy","piece_path":piece_rel})
        total_decoys += decoy_index
        now=utcnow()
        database.upsert_question({"id":question_id,"type":"object_drag","instruction_ko":"물을 마시고 있는 기린을 모두 정답존으로 옮기세요.",
            "instruction_en":"Drag all giraffes that are drinking water.","source":item.get("source","legacy_curated"),
            "source_question_id":str(item.get("question_id",question_id)),"image_path":str(final_image.relative_to(settings.final_dir)),
            "image_width":width,"image_height":height,"difficulty":2,"status":"active" if decoy_index else "inactive","review_status":"approved",
            "reviewer":"legacy-manual-curation","reviewed_at":now,"created_at":now},objects)
        imported += 1
    print(json.dumps({"imported":imported,"targets":sum(len(item["targets"]) for item in manifest),
                      "decoys":total_decoys},ensure_ascii=False))


if __name__=="__main__": main()
