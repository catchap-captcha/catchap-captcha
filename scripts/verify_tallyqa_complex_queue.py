from __future__ import annotations

import json

from PIL import Image

from app.config import settings
from app.main import queue_rows


def main() -> None:
    rows=queue_rows("pending")
    images=[settings.labeling_dir/row["image_path"] for row in rows]
    pieces=[settings.labeling_dir/obj["prepared_piece_path"] for row in rows for obj in row["objects"]]
    bad_mode=empty_alpha=fully_opaque=0
    for path in pieces:
        with Image.open(path) as image:
            if image.mode!="RGBA":bad_mode+=1;continue
            alpha=image.getchannel("A")
            empty_alpha+=alpha.getbbox() is None
            fully_opaque+=alpha.getextrema()==(255,255)
    print(json.dumps({"pending":len(rows),"complex_only":sum(row.get("split")=="test_complex" for row in rows),
        "objects":len(pieces),"missing_images":sum(not path.is_file() for path in images),
        "missing_pieces":sum(not path.is_file() for path in pieces),"bad_mode":bad_mode,
        "empty_alpha":empty_alpha,"fully_opaque":fully_opaque},ensure_ascii=False))


if __name__=="__main__":main()
