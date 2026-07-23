from __future__ import annotations

import argparse
import subprocess
import zipfile
from pathlib import Path


ARCHIVES = {
    "train2014.zip": "http://images.cocodataset.org/zips/train2014.zip",
    "val2014.zip": "http://images.cocodataset.org/zips/val2014.zip",
    "images.zip": "https://cs.stanford.edu/people/rak248/VG_100K_2/images.zip",
    "images2.zip": "https://cs.stanford.edu/people/rak248/VG_100K_2/images2.zip",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    for name, url in ARCHIVES.items():
        target = args.output_dir / name
        subprocess.run(
            ["wget", "-c", url, "-O", str(target)],
            check=True,
        )
        if args.verify:
            with zipfile.ZipFile(target) as archive:
                bad_member = archive.testzip()
                if bad_member:
                    raise RuntimeError(f"Corrupt member in {name}: {bad_member}")
        print(f"Ready: {target}")


if __name__ == "__main__":
    main()
