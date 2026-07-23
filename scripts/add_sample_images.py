from __future__ import annotations

import hashlib
import io
import json
import urllib.request
from urllib.error import HTTPError, URLError
from pathlib import Path

from PIL import Image, ImageOps


URLS = [
    "https://cs.stanford.edu/people/rak248/VG_100K_2/1.jpg",
    "https://cs.stanford.edu/people/rak248/VG_100K/4.jpg",
    "https://cs.stanford.edu/people/rak248/VG_100K_2/10.jpg",
    "https://cs.stanford.edu/people/rak248/VG_100K/100.jpg",
    "https://cs.stanford.edu/people/rak248/VG_100K_2/1000.jpg",
]


def main() -> None:
    output_dir = Path(__file__).resolve().parents[1] / "data" / "processed"
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = output_dir / "sample-manifest.jsonl"
    with manifest.open("w", encoding="utf-8") as handle:
        for url in URLS:
            try:
                with urllib.request.urlopen(url, timeout=30) as response:
                    payload = response.read()
            except (HTTPError, URLError) as exc:
                print(f"Skipped unavailable image: {url} ({exc})")
                continue
            with Image.open(io.BytesIO(payload)) as opened:
                image = ImageOps.fit(
                    ImageOps.exif_transpose(opened).convert("RGB"),
                    (640, 360),
                    method=Image.Resampling.LANCZOS,
                )
            name = "sample-" + hashlib.sha1(url.encode()).hexdigest() + ".jpg"
            image.save(output_dir / name, "JPEG", quality=88, optimize=True)
            handle.write(json.dumps({"file": name, "source": url}) + "\n")
            print(f"Added: {name}")


if __name__ == "__main__":
    main()
