from __future__ import annotations

import json
import secrets
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps


CANVAS_WIDTH = 320
CANVAS_HEIGHT = 180
PIECE_SIZE = 52


@dataclass(frozen=True)
class RenderedChallenge:
    image_name: str
    target_x: int
    target_y: int
    width: int
    height: int
    piece_size: int
    background_path: Path
    piece_path: Path


@dataclass(frozen=True)
class RenderedObjectChallenge:
    image_name: str
    width: int
    height: int
    marker_size: int
    targets: list[dict[str, int]]
    background_path: Path


class ImagePool:
    def __init__(self, image_dir: Path) -> None:
        self.image_dir = image_dir
        self._images: list[Path] = []
        self._last_scan = 0.0

    def _scan(self) -> None:
        self._images = sorted(
            path
            for pattern in ("*.jpg", "*.jpeg", "*.png", "*.webp")
            for path in self.image_dir.glob(pattern)
            if path.is_file()
        )
        self._last_scan = time.monotonic()

    def count(self) -> int:
        if not self._images or time.monotonic() - self._last_scan > 30:
            self._scan()
        return len(self._images)

    def choose(self) -> Path:
        if self.count() == 0:
            raise RuntimeError(f"No processed images found in {self.image_dir}")
        return secrets.choice(self._images)


class GiraffePool:
    def __init__(self, image_dir: Path) -> None:
        self.image_dir = image_dir
        self.manifest_path = image_dir / "manifest.json"
        self._items: list[dict] = []
        self._manifest_mtime = 0.0

    def _scan(self) -> None:
        if not self.manifest_path.exists():
            self._items = []
            return
        mtime = self.manifest_path.stat().st_mtime
        if self._items and mtime == self._manifest_mtime:
            return
        items = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self._items = [
            item
            for item in items
            if item.get("targets") and (self.image_dir / item["file"]).exists()
        ]
        self._manifest_mtime = mtime

    def count(self) -> int:
        self._scan()
        return len(self._items)

    def choose(self) -> dict:
        self._scan()
        if not self._items:
            raise RuntimeError(f"No annotated giraffe images found in {self.image_dir}")
        return secrets.choice(self._items)


def make_piece_mask(size: int = PIECE_SIZE) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    margin = 7
    tab = 8
    draw.rounded_rectangle(
        (margin, margin, size - margin, size - margin), radius=3, fill=255
    )
    draw.ellipse(
        (size // 2 - tab, 0, size // 2 + tab, tab * 2), fill=255
    )
    draw.ellipse(
        (size - tab * 2, size // 2 - tab, size, size // 2 + tab), fill=255
    )
    draw.ellipse(
        (size // 2 - tab, size - tab * 2, size // 2 + tab, size), fill=0
    )
    return mask.filter(ImageFilter.GaussianBlur(radius=0.35))


def render_challenge(
    source_path: Path, challenge_dir: Path, challenge_id: str
) -> RenderedChallenge:
    challenge_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(source_path) as source:
        source = ImageOps.exif_transpose(source).convert("RGB")
        canvas = ImageOps.fit(
            source,
            (CANVAS_WIDTH, CANVAS_HEIGHT),
            method=Image.Resampling.LANCZOS,
        )

    target_x = secrets.randbelow(CANVAS_WIDTH - PIECE_SIZE - 105) + 95
    target_y = secrets.randbelow(CANVAS_HEIGHT - PIECE_SIZE - 20) + 10
    box = (
        target_x,
        target_y,
        target_x + PIECE_SIZE,
        target_y + PIECE_SIZE,
    )
    mask = make_piece_mask()

    crop = canvas.crop(box)
    piece = crop.convert("RGBA")
    piece.putalpha(mask)

    darkened = ImageEnhance.Brightness(crop).enhance(0.28)
    canvas.paste(darkened, (target_x, target_y), mask)
    outline = ImageChops.subtract(mask.filter(ImageFilter.MaxFilter(5)), mask)
    outline_layer = Image.new("RGB", (PIECE_SIZE, PIECE_SIZE), (255, 255, 255))
    canvas.paste(outline_layer, (target_x, target_y), outline.point(lambda p: p // 2))

    background_path = challenge_dir / f"{challenge_id}.bg.jpg"
    piece_path = challenge_dir / f"{challenge_id}.piece.png"
    canvas.save(background_path, "JPEG", quality=88, optimize=True)
    piece.save(piece_path, "PNG", optimize=True)

    return RenderedChallenge(
        image_name=source_path.name,
        target_x=target_x,
        target_y=target_y,
        width=CANVAS_WIDTH,
        height=CANVAS_HEIGHT,
        piece_size=PIECE_SIZE,
        background_path=background_path,
        piece_path=piece_path,
    )


def render_object_challenge(
    item: dict, image_dir: Path, challenge_dir: Path, challenge_id: str
) -> RenderedObjectChallenge:
    challenge_dir.mkdir(parents=True, exist_ok=True)
    source_path = image_dir / item["file"]
    background_path = challenge_dir / f"{challenge_id}.bg.jpg"
    shutil.copyfile(source_path, background_path)
    return RenderedObjectChallenge(
        image_name=item["file"],
        width=640,
        height=360,
        marker_size=44,
        targets=item["targets"],
        background_path=background_path,
    )
