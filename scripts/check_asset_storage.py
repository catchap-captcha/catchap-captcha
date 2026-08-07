#!/usr/bin/env python3
"""자산 저장소가 **실제로** 통하는지 확인한다.

가짜로 흉내 낸 시험은 "통과했는데 실제로는 안 되는" 결과를 만든다. 그래서 이 확인은
**진짜 버킷에 대고** 돌린다. 배포 전에 한 번, 그리고 설정을 바꿀 때마다 돌린다.

    ASSET_STORAGE_BACKEND=object ASSET_BUCKET=... ASSET_S3_ACCESS_KEY=... \\
    ASSET_S3_SECRET_KEY=... python scripts/check_asset_storage.py

★값을 화면에 찍지 않는다. 길이와 통했는지만 보여 준다.
★읽기만 한다 — 버킷에 아무것도 쓰지 않는다. (`--write-probe` 를 주면 임시 키 하나로
  쓰기까지 확인하고 지운다.)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import asset_storage as assets  # noqa: E402
from app.config import settings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default="challenges.jsonl", help="읽어 볼 키")
    ap.add_argument("--write-probe", action="store_true", help="임시 키로 쓰기까지 확인하고 지운다")
    args = ap.parse_args()

    print("설정")
    print(f"  backend    {settings.asset_storage_backend}")
    print(f"  bucket     {settings.asset_bucket or '(비어 있음)'}")
    print(f"  prefix     {settings.asset_key_prefix}")
    print(f"  endpoint   {settings.asset_s3_endpoint}")
    print(f"  access key 길이 {len(settings.asset_s3_access_key)}")
    print(f"  secret key 길이 {len(settings.asset_s3_secret_key)}")
    print()

    try:
        storage = assets.get_asset_storage()
    except Exception as e:  # noqa: BLE001
        print(f"❌ 저장소를 만들지 못했습니다: {e}")
        return 1
    print(f"저장소 구현: {type(storage).__name__}")
    print()

    ok = True

    # ① 있어야 하는 것 — 양성 대조
    st = storage.stat(args.key)
    if st is None:
        print(f"❌ [양성 대조] '{args.key}' 가 없습니다. 자산이 안 올라갔거나 prefix 가 틀립니다.")
        ok = False
    else:
        print(f"✅ [양성 대조] '{args.key}' 있음 · {st.size:,} 바이트")
        head = storage.read_bytes(args.key)[:80]
        print(f"   앞 80바이트: {head[:80]!r}")

    # ② 없어야 하는 것 — 음성 대조
    #    ★이게 없으면 "전부 있다고 답하는 고장난 저장소"도 통과한다.
    missing = "images/__nope__.jpg"
    if storage.stat(missing) is None:
        print(f"✅ [음성 대조] '{missing}' 없음 — 제대로 없다고 답합니다")
    else:
        print(f"❌ [음성 대조] '{missing}' 가 있다고 답합니다. 저장소가 이상합니다.")
        ok = False

    # ③ 경로 탈출 차단
    try:
        assets.validate_key("../../etc/passwd")
        print("❌ [경로 탈출] 막히지 않았습니다")
        ok = False
    except assets.AssetNotFound:
        print("✅ [경로 탈출] 막힙니다")

    # ④ 쓰기 (선택)
    if args.write_probe:
        key = "__write-probe__.txt"
        try:
            storage.write_bytes(key, b"probe")
            back = storage.read_bytes(key)
            print("✅ [쓰기] 올리고 다시 읽었습니다" if back == b"probe" else "❌ [쓰기] 내용이 다릅니다")
            ok = ok and back == b"probe"
        except Exception as e:  # noqa: BLE001
            print(f"❌ [쓰기] 실패: {e}")
            ok = False
        else:
            # 로컬은 파일, 오브젝트는 객체 — 둘 다 지운다
            if isinstance(storage, assets.LocalAssetStorage):
                storage.path(key).unlink(missing_ok=True)
            else:
                storage._s3.delete_object(Bucket=storage._bucket, Key=storage._obj(key))
            print("   임시 키 정리 완료")

    print()
    print("★전부 통과" if ok else "★실패한 항목이 있습니다")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
