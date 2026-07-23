from __future__ import annotations

import argparse
import io
import json
import re
import shutil
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps
from ultralytics import YOLO

from app.config import ROOT_DIR, settings


ARCHIVES={"VG_100K":"images.zip","VG_100K_2":"images2.zip","train2014":"train2014.zip","val2014":"val2014.zip"}
CLASS_ALIASES={
 "person":{"person","people","persons","pedestrian"},"bicycle":{"bicycle","bicycles","bike","bikes"},
 "car":{"car","cars","automobile","automobiles"},"motorcycle":{"motorcycle","motorcycles","motorbike","motorbikes"},
 "airplane":{"airplane","airplanes","plane","planes","aircraft"},"bus":{"bus","buses"},"train":{"train","trains"},
 "truck":{"truck","trucks"},"boat":{"boat","boats","ship","ships"},"traffic light":{"traffic light","traffic lights"},
 "fire hydrant":{"fire hydrant","fire hydrants","hydrant","hydrants"},"stop sign":{"stop sign","stop signs"},
 "bench":{"bench","benches"},"bird":{"bird","birds"},"cat":{"cat","cats"},"dog":{"dog","dogs"},
 "horse":{"horse","horses"},"sheep":{"sheep"},"cow":{"cow","cows","cattle"},"elephant":{"elephant","elephants"},
 "bear":{"bear","bears"},"zebra":{"zebra","zebras"},"giraffe":{"giraffe","giraffes"},
 "backpack":{"backpack","backpacks"},"umbrella":{"umbrella","umbrellas"},"handbag":{"handbag","handbags","purse","purses"},
 "suitcase":{"suitcase","suitcases","luggage"},"frisbee":{"frisbee","frisbees"},"skis":{"ski","skis"},
 "snowboard":{"snowboard","snowboards"},"sports ball":{"ball","balls","sports ball","sports balls"},
 "kite":{"kite","kites"},"baseball bat":{"baseball bat","baseball bats","bat","bats"},
 "baseball glove":{"baseball glove","baseball gloves","glove","gloves"},"skateboard":{"skateboard","skateboards"},
 "surfboard":{"surfboard","surfboards"},"tennis racket":{"tennis racket","tennis rackets","racket","rackets"},
 "bottle":{"bottle","bottles"},"wine glass":{"wine glass","wine glasses"},"cup":{"cup","cups","mug","mugs"},
 "fork":{"fork","forks"},"knife":{"knife","knives"},"spoon":{"spoon","spoons"},"bowl":{"bowl","bowls"},
 "banana":{"banana","bananas"},"apple":{"apple","apples"},"sandwich":{"sandwich","sandwiches"},
 "orange":{"orange","oranges"},"broccoli":{"broccoli"},"carrot":{"carrot","carrots"},"hot dog":{"hot dog","hot dogs"},
 "pizza":{"pizza","pizzas"},"donut":{"donut","donuts","doughnut","doughnuts"},"cake":{"cake","cakes"},
 "chair":{"chair","chairs","seat","seats"},"couch":{"couch","couches","sofa","sofas"},
 "potted plant":{"potted plant","potted plants","plant","plants"},"bed":{"bed","beds"},
 "dining table":{"dining table","dining tables","table","tables"},"toilet":{"toilet","toilets"},
 "tv":{"tv","tvs","television","televisions"},"laptop":{"laptop","laptops"},"mouse":{"computer mouse","computer mice"},
 "remote":{"remote","remotes","remote control","remote controls"},"keyboard":{"keyboard","keyboards"},
 "cell phone":{"cell phone","cell phones","phone","phones"},"microwave":{"microwave","microwaves"},
 "oven":{"oven","ovens"},"toaster":{"toaster","toasters"},"sink":{"sink","sinks"},
 "refrigerator":{"refrigerator","refrigerators","fridge","fridges"},"book":{"book","books"},
 "clock":{"clock","clocks"},"vase":{"vase","vases"},"scissors":{"scissors"},
 "teddy bear":{"teddy bear","teddy bears"},"hair drier":{"hair drier","hair driers","hair dryer","hair dryers"},
 "toothbrush":{"toothbrush","toothbrushes"},
}
KOREAN={"person":"사람","bicycle":"자전거","car":"자동차","motorcycle":"오토바이","airplane":"비행기","bus":"버스",
 "train":"기차","truck":"트럭","boat":"보트","traffic light":"신호등","fire hydrant":"소화전","stop sign":"정지 표지판",
 "bench":"벤치","bird":"새","cat":"고양이","dog":"개","horse":"말","sheep":"양","cow":"소","elephant":"코끼리",
 "bear":"곰","zebra":"얼룩말","giraffe":"기린","backpack":"백팩","umbrella":"우산","handbag":"핸드백",
 "suitcase":"여행가방","frisbee":"프리스비","skis":"스키","snowboard":"스노보드","sports ball":"공","kite":"연",
 "baseball bat":"야구 방망이","baseball glove":"야구 글러브","skateboard":"스케이트보드","surfboard":"서핑보드",
 "tennis racket":"테니스 라켓","bottle":"병","wine glass":"와인잔","cup":"컵","fork":"포크","knife":"칼",
 "spoon":"숟가락","bowl":"그릇","banana":"바나나","apple":"사과","sandwich":"샌드위치","orange":"오렌지",
 "broccoli":"브로콜리","carrot":"당근","hot dog":"핫도그","pizza":"피자","donut":"도넛","cake":"케이크",
 "chair":"의자","couch":"소파","potted plant":"화분","bed":"침대","dining table":"테이블","toilet":"변기",
 "tv":"텔레비전","laptop":"노트북","mouse":"마우스","remote":"리모컨","keyboard":"키보드","cell phone":"휴대전화",
 "microwave":"전자레인지","oven":"오븐","toaster":"토스터","sink":"싱크대","refrigerator":"냉장고","book":"책",
 "clock":"시계","vase":"꽃병","scissors":"가위","teddy bear":"곰인형","hair drier":"헤어드라이어","toothbrush":"칫솔"}
ATTR_KO={"red":"빨간색","blue":"파란색","green":"초록색","yellow":"노란색","black":"검은색","white":"흰색",
 "brown":"갈색","orange":"주황색","pink":"분홍색","gray":"회색","grey":"회색","striped":"줄무늬",
 "wooden":"나무 재질의","small":"작은","large":"큰","tall":"키가 큰","short":"짧은"}
ACTION_FORMS={
 "wear":{"wear","wearing","wears","worn"},"hold":{"hold","holding","holds","held","carry","carrying","carries"},
 "ride":{"ride","riding","rides","ridden"},"sit":{"sit","sitting","sits","seated"},
 "stand":{"stand","standing","stands"},"eat":{"eat","eating","eats"},"drink":{"drink","drinking","drinks"},
 "walk":{"walk","walking","walks"},"play":{"play","playing","plays"},"look":{"look","looking","looks","watching"},
 "park":{"park","parked","parking"},"on":{"on top of"},"under":{"under","beneath"},"behind":{"behind"},
 "front":{"in front of"},"next":{"next to","beside"},
}
ACTION_KO={"wear":"입고 있는","hold":"들고 있는","ride":"타고 있는","sit":"앉아 있는","stand":"서 있는",
 "eat":"먹고 있는","drink":"마시고 있는","walk":"걷고 있는","play":"사용하거나 놀고 있는","look":"바라보는",
 "park":"주차된","on":"위에 있는","under":"아래에 있는","behind":"뒤에 있는","front":"앞에 있는","next":"옆에 있는"}
IGNORED_ATTRS={"one","two","three","four","several","many","group","lovely","visible","pictured","seen"}


def norm(value: str) -> str:
    return re.sub(r"[^a-z0-9 ]+"," ",str(value).lower()).strip()


def names(row: dict) -> list[str]:
    return [norm(v) for v in (row.get("names") or [row.get("name","")]) if v]


def read_zip_json(path: Path, suffix: str) -> list[dict]:
    with zipfile.ZipFile(path) as archive:
        member=next(n for n in archive.namelist() if n.endswith(suffix))
        with archive.open(member) as source: return json.load(source)


def canonical(raw_names: list[str]) -> str | None:
    values=set(raw_names)
    if values & {"man","men","woman","women","boy","boys","girl","girls","child","children","kid","kids","player","players"}:return "person"
    for label,aliases in CLASS_ALIASES.items():
        if values & aliases: return label
    return None


def question_class(question: str) -> str | None:
    q=f" {norm(question)} "
    found=[]
    for label,aliases in CLASS_ALIASES.items():
        for alias in aliases:
            if f" {alias} " in q: found.append((len(alias),label))
    return max(found,default=(0,None))[1]


def bbox(row: dict,width: int,height: int) -> dict | None:
    x=float(row.get("x",0));y=float(row.get("y",0));w=float(row.get("w",row.get("width",0)));h=float(row.get("h",row.get("height",0)))
    if min(w,h)<=0 or x<0 or y<0 or x+w>width+2 or y+h>height+2:return None
    area=w*h/(width*height)
    if area<.006 or area>.62 or w/width<.045 or h/height<.045:return None
    return {"object_key":str(row["object_id"]),"x":x/width,"y":y/height,"width":w/width,"height":h/height,"area_ratio":area,
            "ids":{str(row["object_id"]),*(str(v) for v in row.get("merged_object_ids",[]))}}


def overlap(a: dict,b: dict) -> float:
    x1=max(a["x"],b["x"]);y1=max(a["y"],b["y"]);x2=min(a["x"]+a["width"],b["x"]+b["width"]);y2=min(a["y"]+a["height"],b["y"]+b["height"])
    inter=max(0,x2-x1)*max(0,y2-y1);union=a["area_ratio"]+b["area_ratio"]-inter
    return inter/union if union else 0


def action_in(text: str) -> str | None:
    q=f" {norm(text)} "
    for action,forms in ACTION_FORMS.items():
        if any(f" {form} " in q for form in forms): return action
    return None


def evidence(question: str,label: str,objects: list[dict],attrs: dict[str,set[str]],relations: list[dict],answer: int) -> tuple[set[str],str,str] | None:
    q=f" {norm(question)} ";answer_attrs={a for values in attrs.values() for a in values if a not in IGNORED_ATTRS and f" {a} " in q}
    attr_ids={obj["object_key"] for obj in objects if any(a in answer_attrs for oid in obj["ids"] for a in attrs.get(oid,set()))}
    action=action_in(question);rel_ids=set();rel_object=""
    if action:
        forms=ACTION_FORMS[action]
        all_ids={oid:obj["object_key"] for obj in objects for oid in obj["ids"]}
        for rel in relations:
            subject=str(rel.get("subject",{}).get("object_id"));representative=all_ids.get(subject)
            if not representative:continue
            predicate=norm(rel.get("predicate",""))
            if not predicate:continue
            if not any(form in predicate or predicate in form for form in forms):continue
            obj=rel.get("object",{});obj_names=names(obj);mentioned=next((n for n in obj_names if f" {n} " in q),None)
            obj_attrs=attrs.get(str(obj.get("object_id")),set());attr_ok=not answer_attrs or bool(answer_attrs & obj_attrs)
            if mentioned and attr_ok: rel_ids.add(representative);rel_object=mentioned
    options=[]
    if attr_ids:options.append((attr_ids,"attribute",next(iter(answer_attrs),"")))
    if rel_ids:options.append((rel_ids,"relation",f"{action}:{rel_object}"))
    if attr_ids and rel_ids:options.insert(0,(attr_ids&rel_ids,"combined",f"{next(iter(answer_attrs), '')}:{action}:{rel_object}"))
    return next((option for option in options if len(option[0])==answer),None)


def instruction(label: str,kind: str,detail: str) -> str:
    noun=KOREAN[label]
    if kind=="count_validated":return f"사진 속 질문 조건에 맞는 {noun}을 모두 정답존으로 옮기세요."
    if kind=="attribute":return f"{ATTR_KO.get(detail,detail)} {noun}을 모두 정답존으로 옮기세요."
    parts=detail.split(":");action=parts[-2] if len(parts)>=2 else "";obj=parts[-1] if parts else ""
    obj_ko=next((KOREAN[k] for k,aliases in CLASS_ALIASES.items() if obj in aliases),obj)
    phrase=ACTION_KO.get(action,"조건에 맞는")
    return f"{obj_ko+'을 ' if obj_ko else ''}{phrase} {noun}을 모두 정답존으로 옮기세요."


def box_pixels(obj: dict,width: int,height: int) -> tuple[int,int,int,int]:
    return round(obj["x"]*width),round(obj["y"]*height),round((obj["x"]+obj["width"])*width),round((obj["y"]+obj["height"])*height)


def iou(a,b):
    x1=max(a[0],b[0]);y1=max(a[1],b[1]);x2=min(a[2],b[2]);y2=min(a[3],b[3]);inter=max(0,x2-x1)*max(0,y2-y1)
    union=(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-inter
    return inter/union if union else 0


def segment(model: YOLO,rgb: np.ndarray,objects: list[dict],target_ids: set[str]) -> tuple[list[dict],list[np.ndarray],list[dict]] | None:
    height,width=rgb.shape[:2];result=model.predict(rgb,device=0,imgsz=1024,conf=.30,iou=.55,retina_masks=True,verbose=False)[0]
    detected=[]
    if result.boxes is None or result.masks is None:return None
    for idx,polygon_set in enumerate(result.masks.xy):
        canvas=np.zeros((height,width),np.uint8);polygons=polygon_set if isinstance(polygon_set,list) else [polygon_set]
        for polygon in polygons:
            pts=np.asarray(polygon,dtype=np.int32)
            if len(pts)>=3:cv2.fillPoly(canvas,[pts],1)
        detected.append({"mask":canvas.astype(bool),"box":tuple(int(v) for v in result.boxes.xyxy[idx].detach().cpu().tolist()),
            "label":result.names[int(result.boxes.cls[idx].item())],"confidence":float(result.boxes.conf[idx].item())})
    boxes=[box_pixels(obj,width,height) for obj in objects];pairs=[]
    for oi,box in enumerate(boxes):
        for di,det in enumerate(detected):
            if det["label"]!=objects[oi]["label"]:continue
            score=iou(box,det["box"]);cx=(det["box"][0]+det["box"][2])/2;cy=(det["box"][1]+det["box"][3])/2
            if box[0]<=cx<=box[2] and box[1]<=cy<=box[3]:score+=.35
            score+=det["confidence"]*.1
            if score>=.45:pairs.append((score,oi,di))
    assigned={};used=set()
    for score,oi,di in sorted(pairs,reverse=True):
        if oi not in assigned and di not in used:assigned[oi]=(di,score);used.add(di)
    mandatory={oi for oi,obj in enumerate(objects) if obj["object_key"] in target_ids}
    if not mandatory<=set(assigned):return None
    kept=[];masks=[];metrics=[]
    for oi,(obj,box) in enumerate(zip(objects,boxes)):
        if oi not in assigned:continue
        di,score=assigned[oi];mask=detected[di]["mask"].copy();x1,y1,x2,y2=box;mask[:y1]=False;mask[y2:]=False;mask[:,:x1]=False;mask[:,x2:]=False
        fill=int(mask.sum())/max(1,(x2-x1)*(y2-y1))
        if mask.sum()<300 or not .05<=fill<=.92:
            if oi in mandatory:return None
            continue
        kept.append(obj);masks.append(mask);metrics.append({"confidence":detected[di]["confidence"],"match_score":score,"fill_ratio":fill})
    if not any(obj["object_key"] not in target_ids for obj in kept):return None
    stack=np.stack(masks);overlap_pixels=int((stack.sum(axis=0)>1).sum());union=int((stack.sum(axis=0)>0).sum())
    if overlap_pixels/max(1,union)>.02:return None
    if overlap_pixels:
        overlap_mask=stack.sum(axis=0)>1;yy,xx=np.indices((height,width));dist=[]
        for box in [box_pixels(obj,width,height) for obj in kept]:
            cx=(box[0]+box[2])/2;cy=(box[1]+box[3])/2;dist.append(((xx-cx)/max(1,box[2]-box[0]))**2+((yy-cy)/max(1,box[3]-box[1]))**2)
        dist=np.stack(dist);dist[~stack]=np.inf;owner=np.argmin(dist,axis=0)
        for oi in range(len(masks)):masks[oi][overlap_mask]=owner[overlap_mask]==oi
    return kept,masks,metrics


def main() -> None:
    parser=argparse.ArgumentParser();parser.add_argument("--limit",type=int,default=1000);parser.add_argument("--model",default="yolo11l-seg.pt");parser.add_argument("--candidate-limit",type=int,default=8000);parser.add_argument("--analyze-only",action="store_true");args=parser.parse_args()
    test=json.loads((ROOT_DIR/"data/metadata/test.json").read_text());existing_reviews=set()
    reviewed=settings.labeling_dir/"reviewed.jsonl"
    if reviewed.exists():existing_reviews={str(json.loads(line).get("question_id")) for line in reviewed.read_text().splitlines() if line.strip()}
    raw=[]
    for row in test:
        try:answer=int(row.get("answer"))
        except (TypeError,ValueError):continue
        q=norm(row.get("question",""));label=question_class(q)
        if row.get("issimple") is not False or not 1<=answer<=4 or not label or label=="person" and any(f" {x} " in f" {q} " for x in ("men","women","boys","girls","children","kids")):continue
        image=str(row.get("image",""))
        if not image.startswith(tuple(f"{name}/" for name in ARCHIVES)) or str(row.get("question_id")) in existing_reviews:continue
        raw.append({**row,"answer":answer,"label":label})
    vg_rows=[row for row in raw if str(row["image"]).startswith(("VG_100K/","VG_100K_2/"))]
    image_ids={int(Path(row["image"]).stem) for row in vg_rows}
    vg=ROOT_DIR/"data/annotations/visual_genome"
    image_data={int(r["image_id"]):r for r in read_zip_json(vg/"image_data.json.zip","image_data.json") if int(r["image_id"]) in image_ids}
    object_data={int(r["image_id"]):r.get("objects",[]) for r in read_zip_json(vg/"objects.json.zip","objects.json") if int(r["image_id"]) in image_ids}
    attribute_data={int(r["image_id"]):r.get("attributes",[]) for r in read_zip_json(vg/"attributes.json.zip","attributes.json") if int(r["image_id"]) in image_ids}
    relation_data={int(r["image_id"]):r.get("relationships",[]) for r in read_zip_json(vg/"relationships.json.zip","relationships.json") if int(r["image_id"]) in image_ids}
    coco_refs={str(row["image"]) for row in raw if str(row["image"]).startswith(("train2014/","val2014/"))};coco_data={}
    coco_zip=ROOT_DIR/"data/annotations/coco2014/annotations_trainval2014.zip"
    for split in ("train2014","val2014"):
        data=read_zip_json(coco_zip,f"instances_{split}.json");categories={row["id"]:canonical([norm(row["name"])]) for row in data["categories"]}
        wanted={Path(ref).name:ref for ref in coco_refs if ref.startswith(f"{split}/")};images={row["id"]:row for row in data["images"] if row["file_name"] in wanted}
        for image in images.values():coco_data[wanted[image["file_name"]]]={"width":image["width"],"height":image["height"],"objects":[]}
        for ann in data["annotations"]:
            image=images.get(ann["image_id"]);label=categories.get(ann["category_id"])
            if not image or not label or ann.get("iscrowd"):continue
            ref=wanted[image["file_name"]];x,y,w,h=ann["bbox"]
            coco_data[ref]["objects"].append({"object_id":f"coco_{ann['id']}","names":[label],"x":x,"y":y,"w":w,"h":h})
    candidates=[];used_images=set();rejects=Counter()
    for row in raw:
        image_ref=str(row["image"]);image_id=int(Path(image_ref).stem.split("_")[-1])
        if image_ref in used_images:continue
        is_vg=str(row["image"]).startswith(("VG_100K/","VG_100K_2/"));meta=image_data.get(image_id,{}) if is_vg else coco_data.get(str(row["image"]),{})
        width=int(meta.get("width",0));height=int(meta.get("height",0))
        if min(width,height)<300:rejects["low_resolution"]+=1;continue
        all_objects=[]
        source_objects=object_data.get(image_id,[]) if is_vg else meta.get("objects",[])
        for obj in source_objects:
            obj_label=canonical(names(obj))
            if not obj_label:continue
            box=bbox(obj,width,height)
            if box:all_objects.append({**box,"label":obj_label})
        dedup=[]
        for obj in sorted(all_objects,key=lambda x:x["area_ratio"],reverse=True):
            duplicate=next((kept for kept in dedup if kept["label"]==obj["label"] and overlap(obj,kept)>.72),None)
            if duplicate:duplicate["ids"].update(obj["ids"])
            else:dedup.append(obj)
        target_objects=[obj for obj in dedup if obj["label"]==row["label"]]
        if not 1<=len(target_objects)<=8 or len(target_objects)<row["answer"]:rejects["target_object_count"]+=1;continue
        attrs=defaultdict(set)
        for attr in attribute_data.get(image_id,[]) if is_vg else []:
            for value in attr.get("attributes",[]):attrs[str(attr.get("object_id"))].add(norm(value))
        found=evidence(row["question"],row["label"],target_objects,attrs,relation_data.get(image_id,[]) if is_vg else [],row["answer"])
        if not found and len(target_objects)==row["answer"]:
            found=({obj["object_key"] for obj in target_objects},"count_validated","")
        if not found:rejects["no_evidence"]+=1;continue
        target_ids,kind,detail=found
        decoys=[obj for obj in dedup if obj["object_key"] not in target_ids]
        selected_targets=[obj for obj in target_objects if obj["object_key"] in target_ids]
        decoys=sorted(decoys,key=lambda obj:obj["area_ratio"],reverse=True)
        selected=selected_targets[:]
        for decoy in decoys:
            if len(selected)>=min(8,len(selected_targets)+3):break
            if any(overlap(decoy,other)>.42 for other in selected):continue
            selected.append(decoy)
        if len(selected)<=len(selected_targets):rejects["no_decoy"]+=1;continue
        candidates.append({"row":row,"image_id":image_id,"objects":selected,"target_ids":target_ids,"kind":kind,"detail":detail})
        used_images.add(image_ref)
        if len(candidates)>=args.candidate_limit:break
    print(json.dumps({"complex_numeric_source":len(raw),"annotation_candidates":len(candidates),"pre_rejects":rejects},ensure_ascii=False),flush=True)
    if args.analyze_only:return
    model=YOLO(args.model);batch=settings.labeling_dir/"tallyqa_complex_1000";images_dir=batch/"images";pieces_dir=batch/"pieces"
    if batch.exists():shutil.rmtree(batch)
    images_dir.mkdir(parents=True);pieces_dir.mkdir(parents=True)
    archives={name:zipfile.ZipFile(ROOT_DIR/"data/raw"/filename) for name,filename in ARCHIVES.items()};queue=[];quality_rejected=Counter()
    try:
        for number,candidate in enumerate(candidates,1):
            row=candidate["row"];image_ref=row["image"]
            try:payload=archives[image_ref.split("/",1)[0]].read(image_ref)
            except KeyError:quality_rejected["missing_image"]+=1;continue
            with Image.open(io.BytesIO(payload)) as source:
                rgb=np.array(ImageOps.exif_transpose(source).convert("RGB"))
            gray=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY);sharpness=float(cv2.Laplacian(gray,cv2.CV_64F).var());contrast=float(gray.std())
            if sharpness<85 or contrast<28:quality_rejected["blur_or_low_contrast"]+=1;continue
            segmented=segment(model,rgb,candidate["objects"],candidate["target_ids"])
            if not segmented:quality_rejected["segmentation_failed"]+=1;continue
            segmented_objects,masks,metrics=segmented;question_id=str(row["question_id"]);image_name=f"{candidate['image_id']}.jpg"
            Image.fromarray(rgb).save(images_dir/image_name,"JPEG",quality=95,optimize=True)
            output_objects=[]
            for obj,mask,metric in zip(segmented_objects,masks,metrics):
                ys,xs=np.where(mask);x1,x2=max(0,xs.min()-2),min(rgb.shape[1],xs.max()+3);y1,y2=max(0,ys.min()-2),min(rgb.shape[0],ys.max()+3)
                piece_name=f"{question_id}-{obj['object_key']}.png";rgba=np.dstack((rgb,(mask*255).astype(np.uint8)))[y1:y2,x1:x2]
                Image.fromarray(rgba,"RGBA").save(pieces_dir/piece_name,"PNG",optimize=True)
                output_objects.append({"object_key":obj["object_key"],"label":obj["label"],"x":obj["x"],"y":obj["y"],"width":obj["width"],"height":obj["height"],
                    "area_ratio":obj["area_ratio"],"role":"target" if obj["object_key"] in candidate["target_ids"] else "decoy",
                    "prepared_piece_path":f"tallyqa_complex_1000/pieces/{piece_name}","mask_quality":metric})
            queue.append({"queue_id":f"tallyqa_complex_{question_id}","question_id":question_id,"image_id":candidate["image_id"],
                "image_path":f"tallyqa_complex_1000/images/{image_name}","question_en":row["question"],
                "instruction_ko":instruction(row["label"],candidate["kind"],candidate["detail"]),"expected_target_count":row["answer"],
                "source":"tallyqa_visual_genome","split":"test_complex","target_label":row["label"],"action":candidate["kind"],
                "qualifier":candidate["detail"],"relationship_hints":[],"objects":output_objects,"review_status":"pending",
                "difficulty":3,"translation_status":"template_requires_review","quality":{"sharpness":sharpness,"contrast":contrast}})
            if len(queue)%25==0:print(json.dumps({"screened":number,"approved_for_manual_queue":len(queue),"goal":args.limit},ensure_ascii=False),flush=True)
            if len(queue)>=args.limit:break
    finally:
        for archive in archives.values():archive.close()
    if len(queue)<args.limit:raise RuntimeError(f"Only {len(queue)} quality candidates passed; need {args.limit}")
    queue_path=settings.labeling_dir/"queue.jsonl";backup=settings.labeling_dir/"queue.before-tallyqa-complex-1000.jsonl"
    if queue_path.exists() and not backup.exists():shutil.copy2(queue_path,backup)
    queue_path.write_text("".join(json.dumps(row,ensure_ascii=False)+"\n" for row in queue))
    all_path=settings.labeling_dir/"relation_candidates_all.jsonl";all_rows=[json.loads(x) for x in all_path.read_text().splitlines() if x.strip()]
    ids={row["queue_id"] for row in queue};all_rows=[row for row in all_rows if row.get("queue_id") not in ids]+queue
    all_path.write_text("".join(json.dumps(row,ensure_ascii=False)+"\n" for row in all_rows))
    summary={"queued":len(queue),"source_complex_only":True,"quality_rejected":quality_rejected,"classes":Counter(row["target_label"] for row in queue)}
    (batch/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2));print(json.dumps(summary,ensure_ascii=False),flush=True)


if __name__=="__main__":main()
