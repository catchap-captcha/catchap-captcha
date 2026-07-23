from __future__ import annotations

import argparse
import hashlib
import io
import json
import random
import zipfile
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageOps, ImageStat, UnidentifiedImageError


ARCHIVE_BY_ROOT = {
    "train2014": "train2014.zip",
    "val2014": "val2014.zip",
    "VG_100K": "images.zip",
    "VG_100K_2": "images2.zip",
}


def collect_candidates(metadata_dir: Path, seed: int) -> list[str]:
    scores: dict[str, int] = defaultdict(int)
    for split in ("train", "test"):
        path = metadata_dir / f"{split}.json"
        for row in json.loads(path.read_text(encoding="utf-8")):
            image_path = row["image"]
            scores[image_path] += 2 if not row.get("issimple", True) else 1
    candidates = list(scores)
    random.Random(seed).shuffle(candidates)
    candidates.sort(key=scores.get, reverse=True)
    return candidates


def quality_ok(image: Image.Image) -> bool:
    width, height = image.size
    if width < 320 or height < 180:
        return False
    ratio = width / height
    if ratio < 1.1 or ratio > 2.6:
        return False
    grayscale = image.convert("L").resize((64, 36))
    stats = ImageStat.Stat(grayscale)
    mean = stats.mean[0]
    contrast = stats.stddev[0]
    return 28 <= mean <= 228 and contrast >= 24


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metadata-dir", type=Path, default=Path("data/metadata"))
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/processed"))
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=328)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    archives: dict[str, zipfile.ZipFile] = {}
    for name in set(ARCHIVE_BY_ROOT.values()):
        path = args.raw_dir / name
        if not path.exists() or not zipfile.is_zipfile(path):
            print(f"Skipping unavailable archive: {path}")
            continue
        archives[name] = zipfile.ZipFile(path)
    if not archives:
        raise RuntimeError("No complete source archives are available")
    available = {name: set(archive.namelist()) for name, archive in archives.items()}
    manifest_path = args.output_dir / "manifest.jsonl"
    accepted = 0
    seen_content: set[str] = set()

    with manifest_path.open("w", encoding="utf-8") as manifest:
        for source_path in collect_candidates(args.metadata_dir, args.seed):
            root = source_path.split("/", 1)[0]
            archive_name = ARCHIVE_BY_ROOT.get(root)
            if (
                not archive_name
                or archive_name not in archives
                or source_path not in available[archive_name]
            ):
                continue
            try:
                raw = archives[archive_name].read(source_path)
                with Image.open(io.BytesIO(raw)) as opened:
                    image = ImageOps.exif_transpose(opened).convert("RGB")
                    if not quality_ok(image):
                        continue
                    processed = ImageOps.fit(
                        image,
                        (640, 360),
                        method=Image.Resampling.LANCZOS,
                    )
                    output = io.BytesIO()
                    processed.save(output, "JPEG", quality=88, optimize=True)
            except (OSError, UnidentifiedImageError):
                continue

            payload = output.getvalue()
            digest = hashlib.sha256(payload).hexdigest()
            if digest in seen_content:
                continue
            seen_content.add(digest)
            name = hashlib.sha1(source_path.encode()).hexdigest() + ".jpg"
            (args.output_dir / name).write_bytes(payload)
            manifest.write(
                json.dumps(
                    {"file": name, "source": source_path, "sha256": digest},
                    ensure_ascii=True,
                )
                + "\n"
            )
            accepted += 1
            if accepted % 100 == 0:
                print(f"Processed {accepted}/{args.limit}")
            if accepted >= args.limit:
                break

    for archive in archives.values():
        archive.close()
    if accepted == 0:
        raise RuntimeError("No images were processed; verify archive paths and contents")
    print(f"Finished: {accepted} images in {args.output_dir}")


if __name__ == "__main__":
    main()
