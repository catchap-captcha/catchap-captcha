"""문항 자산(이미지·조각·매니페스트)을 어디에 두는가.

`ASSET_STORAGE_BACKEND=local` 이면 지금과 **100% 같은 동작**(로컬 디스크)이고,
`object` 면 오브젝트 스토리지(S3 호환)에 둔다.

★왜 필요한가 — 지금은 `data/final` 367MB(파일 8,238개)를 **이미지에 구워** 넣는다.
  그런데 그 자산은 `.gitignore` 로 막혀 있어 **git 에 없다.** 그래서 GitHub Actions 가
  이미지를 구울 수 없다(`COPY data/final` 이 "그런 파일 없음"으로 실패).
  자산을 저장소 밖으로 빼면 **이미지가 작아지고, 자산이 바뀌어도 재빌드가 필요 없다.**

★백엔드(`catchap-backend` 의 `app/services/media_storage.py`)와 **같은 방식**이다.
  두 서비스가 서로 다른 방식을 쓰면 한쪽에서 배운 것이 다른 쪽에 안 쓰인다.
  설정 이름도 같은 모양으로 맞췄다(`<도메인>_STORAGE_BACKEND` · `<도메인>_BUCKET` …).

★키(key)는 `data/final` 안의 **상대 경로**다 — `images/tq_1.jpg` · `pieces/tq_1-3.png` ·
  `challenges.jsonl`. DB 에 저장된 `image_path` · `piece_path` 가 그대로 키가 되므로
  **데이터를 고칠 필요가 없다.**
"""

from __future__ import annotations

import io
import mimetypes
import re
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO, Iterator, Protocol

from .config import settings

_CHUNK = 1024 * 1024  # 1MiB — 버킷에서 읽어 흘려보낼 때의 조각 크기
_KEY_RE = re.compile(r"[A-Za-z0-9._/\-]+")


class AssetNotFound(Exception):
    """자산이 없다. 호출부가 404 로 바꾼다."""


@dataclass(frozen=True)
class AssetStat:
    size: int


class AssetStorage(Protocol):
    def read_bytes(self, key: str) -> bytes: ...
    def write_bytes(self, key: str, data: bytes) -> int: ...
    def stat(self, key: str) -> AssetStat | None: ...
    def open_stream(self, key: str) -> Iterator[bytes]: ...


def validate_key(key: str) -> str:
    """`data/final` 안을 벗어나는 키를 막는다.

    ★예전 `safe_asset()` 이 하던 일이다. 로컬 파일일 때는 `resolve()` 후 부모를 봤지만,
      버킷에는 그럴 파일이 없으므로 **키 문자열 자체를 검사**한다.
      `..` 한 조각만 허용해도 다른 서비스의 객체까지 읽힌다.

    ★백엔드 `media_storage._validate_key()` 와 **같은 규칙**이다 —
      절대 경로 거부 · `..` 거부 · 허용 글자 `[A-Za-z0-9._/-]` 만.
      호출부가 id+확장자로만 키를 만들지만 저장소 계층에서 한 번 더 막는다(방어적 이중화).

    ★글자 제한이 멀쩡한 자산을 막지 않는지 확인했다 — 2026-08-07 실측으로
      운영 파드 안 `data/final` 의 **8,238개 전부** 이 글자들만 쓴다.
    """
    k = str(key).strip()
    if not k or k.startswith("/") or ".." in k.split("/"):
        raise AssetNotFound(f"잘못된 자산 키: {key!r}")
    if not _KEY_RE.fullmatch(k):
        raise AssetNotFound(f"자산 키에 허용되지 않는 문자: {key!r}")
    return k


class LocalAssetStorage:
    """지금까지와 같은 로컬 디스크. 개발과 시험은 이쪽을 쓴다."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def path(self, key: str) -> Path:
        return self._root / validate_key(key)

    def read_bytes(self, key: str) -> bytes:
        p = self.path(key)
        if not p.is_file():
            raise AssetNotFound(key)
        return p.read_bytes()

    def write_bytes(self, key: str, data: bytes) -> int:
        p = self.path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return len(data)

    def stat(self, key: str) -> AssetStat | None:
        p = self.path(key)
        return AssetStat(size=p.stat().st_size) if p.is_file() else None

    def open_stream(self, key: str) -> Iterator[bytes]:
        p = self.path(key)
        if not p.is_file():
            raise AssetNotFound(key)
        with open(p, "rb") as f:
            while chunk := f.read(_CHUNK):
                yield chunk


class ObjectAssetStorage:
    """S3 호환 API 로 버킷에 둔다.

    ★boto3 클라이언트를 요청마다 만들지 않고 인스턴스가 하나를 들고 쓴다
      (`get_asset_storage()` 가 lru_cache 로 싱글턴을 준다).
    """

    def __init__(self, *, bucket: str, prefix: str, endpoint: str, region: str,
                 access_key: str, secret_key: str) -> None:
        import boto3  # 지연 임포트 — local 백엔드만 쓰는 환경에서는 설치조차 필요 없다
        from botocore.config import Config

        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            # SigV4 명시 — 카카오클라우드 Object Storage 가 요구한다.
            config=Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"}),
        )

    def _obj(self, key: str) -> str:
        k = validate_key(key)
        return f"{self._prefix}/{k}" if self._prefix else k

    def read_bytes(self, key: str) -> bytes:
        from botocore.exceptions import ClientError

        try:
            r = self._s3.get_object(Bucket=self._bucket, Key=self._obj(key))
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NoSuchBucket"):
                raise AssetNotFound(key) from e
            raise
        body = r["Body"]
        try:
            return body.read()
        finally:
            body.close()

    def write_bytes(self, key: str, data: bytes) -> int:
        self._s3.upload_fileobj(io.BytesIO(data), self._bucket, self._obj(key))
        return len(data)

    def stat(self, key: str) -> AssetStat | None:
        from botocore.exceptions import ClientError

        try:
            r = self._s3.head_object(Bucket=self._bucket, Key=self._obj(key))
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise
        return AssetStat(size=int(r["ContentLength"]))

    def open_stream(self, key: str) -> Iterator[bytes]:
        from botocore.exceptions import ClientError

        try:
            r = self._s3.get_object(Bucket=self._bucket, Key=self._obj(key))
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NoSuchBucket"):
                raise AssetNotFound(key) from e
            raise
        body = r["Body"]
        try:
            while chunk := body.read(_CHUNK):
                yield chunk
        finally:
            body.close()


@lru_cache(maxsize=1)
def get_asset_storage() -> AssetStorage:
    """설정에 따라 구현을 고른다.

    ★`object` 인데 필수 설정이 비어 있으면 **로컬로 조용히 떨어지지 않고 예외를 낸다.**
      조용히 떨어지면 파드는 멀쩡히 뜨고 자산만 통째로 사라진다 — 그게 제일 늦게 드러난다.
      (0807 에 백엔드에서 실제로 그 일이 있었다: `MEDIA_STORAGE_BACKEND="s3"` 오타로
       미디어 기능이 전부 죽었는데 기동은 정상이라 로그에 한 줄도 안 남았다.)
    """
    backend = (settings.asset_storage_backend or "local").strip().lower()
    if backend == "local":
        return LocalAssetStorage(settings.final_dir)
    if backend == "object":
        missing = [name for name, value in (
            ("ASSET_BUCKET", settings.asset_bucket),
            ("ASSET_S3_ENDPOINT", settings.asset_s3_endpoint),
            ("ASSET_S3_ACCESS_KEY", settings.asset_s3_access_key),
            ("ASSET_S3_SECRET_KEY", settings.asset_s3_secret_key),
        ) if not value]
        if missing:
            raise RuntimeError(
                "ASSET_STORAGE_BACKEND=object 인데 다음 설정이 비어 있습니다: " + ", ".join(missing)
            )
        return ObjectAssetStorage(
            bucket=settings.asset_bucket, prefix=settings.asset_key_prefix,
            endpoint=settings.asset_s3_endpoint, region=settings.asset_s3_region,
            access_key=settings.asset_s3_access_key, secret_key=settings.asset_s3_secret_key,
        )
    raise RuntimeError(f"ASSET_STORAGE_BACKEND 값이 올바르지 않습니다: {backend!r} (local|object)")


def reset_asset_storage_cache() -> None:
    """시험에서 설정을 바꾼 뒤 부르는 것. 운영 경로에서는 쓰지 않는다."""
    get_asset_storage.cache_clear()


# ── 편의 함수 ─────────────────────────────────────────────────────────────

def exists(key: str) -> bool:
    try:
        return get_asset_storage().stat(key) is not None
    except AssetNotFound:
        return False


def read_bytes(key: str) -> bytes:
    return get_asset_storage().read_bytes(key)


def write_bytes(key: str, data: bytes) -> int:
    return get_asset_storage().write_bytes(key, data)


def read_text(key: str) -> str | None:
    """없으면 None. `challenges.jsonl` 처럼 '아직 없을 수 있는' 파일에 쓴다."""
    try:
        return get_asset_storage().read_bytes(key).decode("utf-8")
    except AssetNotFound:
        return None


def write_text(key: str, text: str) -> None:
    write_bytes(key, text.encode("utf-8"))


@contextmanager
def local_read(key: str) -> Iterator[Path]:
    """자산을 **로컬 파일 경로로** 잠깐 빌려 준다. 블록을 나가면 정리한다.

    왜 필요한가 — PIL 같은 라이브러리는 파일 경로를 받는 편이 편하다. 버킷에 있는 객체는
    경로가 없으므로 임시로 내려받아 준다. **로컬 백엔드에서는 이미 파일이라 복사하지 않고
    원본 경로를 그대로 준다** — 쓸데없이 두 번 쓰지 않기 위해서다.

    ★로컬 경로를 그대로 주므로 **호출부는 이 파일을 수정·삭제하면 안 된다.** 읽기 전용이다.
    """
    storage = get_asset_storage()
    if isinstance(storage, LocalAssetStorage):
        p = storage.path(key)
        if not p.is_file():
            raise AssetNotFound(key)
        yield p
        return

    import tempfile

    suffix = Path(validate_key(key)).suffix
    d = Path(tempfile.mkdtemp(prefix="catchap-asset-"))
    tmp = d / f"obj{suffix}"
    try:
        tmp.write_bytes(storage.read_bytes(key))
        yield tmp
    finally:
        tmp.unlink(missing_ok=True)
        try:
            d.rmdir()
        except OSError:
            pass


def guess_media_type(key: str) -> str:
    return mimetypes.guess_type(validate_key(key))[0] or "application/octet-stream"


def asset_response(key: str, *, cache_control: str = "private, max-age=300"):
    """자산을 HTTP 응답으로 돌려준다.

    ★로컬 백엔드에서는 예전과 똑같이 `FileResponse` 를 쓴다 — starlette 가 Range·ETag·
      Last-Modified 를 알아서 처리해 준다. **동작을 바꾸지 않는 것이 목적**이다.
    ★오브젝트 백엔드에서는 starlette 가 그 파일을 모르므로 조각으로 읽어 흘려보낸다.
      파일 전체를 메모리에 올리지 않는다.
    """
    from fastapi import HTTPException
    from fastapi.responses import FileResponse, StreamingResponse

    storage = get_asset_storage()
    try:
        if isinstance(storage, LocalAssetStorage):
            p = storage.path(key)
            if not p.is_file():
                raise AssetNotFound(key)
            return FileResponse(p, headers={"Cache-Control": cache_control})

        st = storage.stat(key)
        if st is None:
            raise AssetNotFound(key)
        return StreamingResponse(
            storage.open_stream(key),
            media_type=guess_media_type(key),
            headers={"Cache-Control": cache_control, "Content-Length": str(st.size)},
        )
    except AssetNotFound:
        raise HTTPException(404, "Asset not found")
