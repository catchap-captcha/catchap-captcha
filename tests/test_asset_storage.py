"""자산 저장소 — 로컬 백엔드 동작과 경로 탈출 차단.

★오브젝트 백엔드는 여기서 시험하지 않는다. 진짜 버킷이 있어야 의미가 있고,
  가짜로 흉내 내면 **통과했는데 실제로는 안 되는** 시험이 된다.
  대신 `scripts/check_asset_storage.py` 로 실제 버킷에 대고 확인한다.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.fixture()
def local_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """설정을 tmp_path 로 돌려놓은 저장소. 시험마다 캐시를 비운다."""
    monkeypatch.setenv("ASSET_STORAGE_BACKEND", "local")
    monkeypatch.setenv("FINAL_DIR", str(tmp_path))

    from app import config as config_module

    importlib.reload(config_module)
    from app import asset_storage as module

    importlib.reload(module)
    module.reset_asset_storage_cache()
    yield module
    module.reset_asset_storage_cache()


def test_쓰고_읽으면_그대로다(local_store, tmp_path: Path):
    local_store.write_bytes("images/tq_1.jpg", b"hello-image")
    assert local_store.read_bytes("images/tq_1.jpg") == b"hello-image"
    # ★실제로 파일이 생겼는지도 본다 — "읽힌다"만 보면 메모리에만 있어도 통과한다.
    assert (tmp_path / "images" / "tq_1.jpg").read_bytes() == b"hello-image"


def test_하위폴더가_없어도_만들어_준다(local_store, tmp_path: Path):
    assert not (tmp_path / "pieces").exists()
    local_store.write_bytes("pieces/tq_1-3.png", b"x")
    assert (tmp_path / "pieces" / "tq_1-3.png").is_file()


def test_없는_것은_AssetNotFound(local_store):
    with pytest.raises(local_store.AssetNotFound):
        local_store.read_bytes("images/없는것.jpg")
    assert local_store.exists("images/없는것.jpg") is False


def test_read_text_는_없으면_None(local_store):
    assert local_store.read_text("challenges.jsonl") is None
    local_store.write_text("challenges.jsonl", '{"a":1}\n')
    assert local_store.read_text("challenges.jsonl") == '{"a":1}\n'


@pytest.mark.parametrize(
    "bad",
    [
        "../secrets.env",
        "images/../../etc/passwd",
        "/etc/passwd",
        "images/./../../x",
        "..",
        "",
        "images\\..\\..\\x",
        "images/한글.jpg",
        "images/a b.jpg",
    ],
)
def test_경로_탈출은_막는다(local_store, bad):
    """★예전 safe_asset() 이 하던 방어. 한 조각만 새도 남의 객체까지 읽힌다."""
    with pytest.raises(local_store.AssetNotFound):
        local_store.validate_key(bad)


def test_정상_키는_통과한다(local_store):
    assert local_store.validate_key("images/tq_1.jpg") == "images/tq_1.jpg"
    assert local_store.validate_key("challenges.jsonl") == "challenges.jsonl"
    assert local_store.validate_key("pieces/tq_12-3.png") == "pieces/tq_12-3.png"


def test_local_read_는_원본_경로를_준다(local_store, tmp_path: Path):
    """로컬에서는 복사하지 않는다 — 367MB 를 쓸데없이 두 번 쓰지 않기 위해서."""
    local_store.write_bytes("images/a.png", b"zzz")
    with local_store.local_read("images/a.png") as p:
        assert p == tmp_path / "images" / "a.png"
        assert p.read_bytes() == b"zzz"


def test_설정이_틀리면_기동에서_터진다(monkeypatch: pytest.MonkeyPatch):
    """★조용히 로컬로 떨어지면 파드는 멀쩡히 뜨고 자산만 사라진다 — 그게 제일 늦게 드러난다."""
    monkeypatch.setenv("ASSET_STORAGE_BACKEND", "object")
    monkeypatch.setenv("ASSET_BUCKET", "")

    from app import config as config_module

    importlib.reload(config_module)
    from app import asset_storage as module

    importlib.reload(module)
    module.reset_asset_storage_cache()
    with pytest.raises(RuntimeError) as e:
        module.get_asset_storage()
    assert "ASSET_BUCKET" in str(e.value)
    module.reset_asset_storage_cache()


def test_이상한_백엔드_이름도_터진다(monkeypatch: pytest.MonkeyPatch):
    """0807 백엔드에서 실제로 있었던 일 — 'object' 라고 써야 하는데 's3' 로 적혀 있었다."""
    monkeypatch.setenv("ASSET_STORAGE_BACKEND", "s3")

    from app import config as config_module

    importlib.reload(config_module)
    from app import asset_storage as module

    importlib.reload(module)
    module.reset_asset_storage_cache()
    with pytest.raises(RuntimeError) as e:
        module.get_asset_storage()
    assert "local|object" in str(e.value)
    module.reset_asset_storage_cache()


# ── 기동 시험 ────────────────────────────────────────────────────────────
# ★2026-08-07 실제로 터진 것을 잡는 시험.
#   `from . import asset_storage as assets` 로 넣었는데 main.py 1135줄에
#   `assets = settings.static_dir/"assets"` 가 있어 ★모듈이 Path 로 덮였다.
#   파드가 기동에서 죽었다 — AttributeError: 'PosixPath' has no attribute 'get_asset_storage'.
#
#   ★기존 시험 53개가 이걸 못 잡았다. lifespan 을 안 거치고,
#     덮어쓰는 줄이 정적 파일 마운트 코드라 시험에서 실행되지 않았다.
#     그래서 ★"앱을 실제로 띄워 보는" 시험을 둔다.

def test_앱이_실제로_기동한다(tmp_path, monkeypatch):
    """★TestClient 로 lifespan 까지 돌린다. 이름이 겹치면 여기서 터진다."""
    monkeypatch.setenv("ASSET_STORAGE_BACKEND", "local")
    monkeypatch.setenv("FINAL_DIR", str(tmp_path / "final"))
    monkeypatch.setenv("LABELING_DIR", str(tmp_path / "labeling"))
    monkeypatch.setenv("RUNTIME_DIR", str(tmp_path / "runtime"))
    # ★★static_dir 이 ★실제로 있어야 한다.
    #   main.py 끝에 `if settings.static_dir.exists():` 안에서
    #   `assets = settings.static_dir/"assets"` 로 모듈 수준 이름을 덮는 코드가 있다.
    #   그 폴더가 없으면 그 줄이 아예 안 돌아 ★고장을 지나쳐 버린다(2026-08-07 실측).
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<html></html>", encoding="utf-8")
    monkeypatch.setenv("STATIC_DIR", str(static))

    import importlib
    from fastapi.testclient import TestClient

    from app import config as config_module
    importlib.reload(config_module)
    from app import asset_storage as storage_module
    importlib.reload(storage_module)
    storage_module.reset_asset_storage_cache()
    from app import main as main_module
    importlib.reload(main_module)

    # ★DB 는 이 시험의 대상이 아니다. CI 에 MySQL 이 없으므로 초기화만 막는다.
    #   막는 것은 DB 하나뿐이고, 자산 저장소 확인은 그대로 돈다.
    monkeypatch.setattr(main_module.database, "initialize", lambda *a, **k: None)

    # ★lifespan 이 여기서 돈다 — 자산 저장소 설정 확인도 그 안에서 한다.
    with TestClient(main_module.app) as client:
        assert client.get("/healthz").status_code in (200, 404)

    storage_module.reset_asset_storage_cache()


def test_asset_storage_모듈이_다른_이름에_덮이지_않는다():
    """★main.py 안에서 asset_storage 모듈을 가리키는 이름이 살아 있는지 본다."""
    import types

    from app import main as main_module

    assert isinstance(main_module.asset_store, types.ModuleType), (
        "asset_store 가 모듈이 아니다 — 같은 이름의 변수가 덮어썼을 수 있다"
    )
    assert hasattr(main_module.asset_store, "get_asset_storage")
