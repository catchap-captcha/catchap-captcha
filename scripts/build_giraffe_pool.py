from __future__ import annotations

import argparse
import io
import json
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


ARCHIVE_BY_ROOT = {
    "train2014": "train2014.zip",
    "val2014": "val2014.zip",
    "VG_100K": "images.zip",
    "VG_100K_2": "images2.zip",
}
CANVAS_SIZE = (640, 360)
TARGETS_BY_SOURCE = {
    "VG_100K_2/2392419.jpg": [
        {"x": 198, "y": 178, "width": 128, "height": 112},
    ],
    "VG_100K_2/2394969.jpg": [
        {"x": 92, "y": 205, "width": 86, "height": 112},
    ],
    "train2014/COCO_train2014_000000057298.jpg": [
        {"x": 205, "y": 218, "width": 118, "height": 116},
    ],
    "val2014/COCO_val2014_000000015085.jpg": [
        {"x": 250, "y": 122, "width": 158, "height": 154},
    ],
    "val2014/COCO_val2014_000000284296.jpg": [
        {"x": 98, "y": 145, "width": 90, "height": 92},
        {"x": 250, "y": 156, "width": 86, "height": 94},
        {"x": 370, "y": 166, "width": 96, "height": 96},
    ],
}


def candidates(metadata_dir: Path) -> dict[str, dict]:
    matches: dict[str, dict] = {}
    for split in ("train", "test"):
        rows = json.loads((metadata_dir / f"{split}.json").read_text())
        for row in rows:
            question = row["question"].lower()
            answer = row["answer"]
            is_drinking = "giraffe" in question and (
                "drinking" in question or "leaning down to drink" in question
            )
            if (
                is_drinking
                and "not drinking" not in question
                and isinstance(answer, int)
                and answer > 0
            ):
                matches[row["image"]] = {
                    "source": row["image"],
                    "question": row["question"],
                    "answer": answer,
                    "split": split,
                }
    return matches


def fit_with_letterbox(image: Image.Image) -> tuple[Image.Image, dict[str, int]]:
    contained = ImageOps.contain(image, CANVAS_SIZE, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", CANVAS_SIZE, (23, 32, 38))
    x = (CANVAS_SIZE[0] - contained.width) // 2
    y = (CANVAS_SIZE[1] - contained.height) // 2
    canvas.paste(contained, (x, y))
    return canvas, {"x": x, "y": y, "width": contained.width, "height": contained.height}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metadata-dir", type=Path, default=Path("data/metadata"))
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument(
        "--output-dir", type=Path, default=Path("data/giraffe_drinking")
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    archives = {
        name: zipfile.ZipFile(args.raw_dir / name)
        for name in set(ARCHIVE_BY_ROOT.values())
    }
    manifest: list[dict] = []
    for source, row in sorted(candidates(args.metadata_dir).items()):
        root = source.split("/", 1)[0]
        payload = archives[ARCHIVE_BY_ROOT[root]].read(source)
        with Image.open(io.BytesIO(payload)) as opened:
            original = ImageOps.exif_transpose(opened).convert("RGB")
            original_size = {"width": original.width, "height": original.height}
            image, content_rect = fit_with_letterbox(original)
        output_name = source.replace("/", "_")
        output_path = args.output_dir / output_name
        image.save(output_path, "JPEG", quality=92, optimize=True)
        manifest.append(
            {
                **row,
                "file": output_name,
                "original_size": original_size,
                "content_rect": content_rect,
                "targets": TARGETS_BY_SOURCE[source],
            }
        )
        print(f"Extracted: {source}")

    for archive in archives.values():
        archive.close()
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    sheet = Image.new("RGB", (960, 720), (238, 242, 243))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for index, item in enumerate(manifest):
        with Image.open(args.output_dir / item["file"]) as image:
            thumbnail = image.resize((448, 252), Image.Resampling.LANCZOS)
        column = index % 2
        row = index // 2
        x = 20 + column * 470
        y = 20 + row * 230
        preview = thumbnail.resize((398, 224))
        preview_draw = ImageDraw.Draw(preview)
        scale_x = 398 / CANVAS_SIZE[0]
        scale_y = 224 / CANVAS_SIZE[1]
        for target in item["targets"]:
            preview_draw.rectangle(
                (
                    round(target["x"] * scale_x),
                    round(target["y"] * scale_y),
                    round((target["x"] + target["width"]) * scale_x),
                    round((target["y"] + target["height"]) * scale_y),
                ),
                outline=(88, 255, 164),
                width=3,
            )
        sheet.paste(preview, (x, y))
        label = f"{index + 1}. {item['file']} | drinking={item['answer']}"
        draw.rectangle((x, y + 198, x + 398, y + 224), fill=(23, 32, 38))
        draw.text((x + 7, y + 205), label, fill=(255, 255, 255), font=font)
    sheet.save(args.output_dir / "contact-sheet.jpg", "JPEG", quality=90)
    print(f"Finished: {len(manifest)} confirmed images")


if __name__ == "__main__":
    main()
