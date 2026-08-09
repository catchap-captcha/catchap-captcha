# -*- coding: utf-8 -*-
"""금고 로더가 ★설정을 만들기 전에 도는지 확인한다.

로더 자체는 `catchap-backend` 에서 이미 시험한 코드를 그대로 옮긴 것이라
여기서 다시 시험하지 않는다. ★이 저장소에서 새로 생긴 것은 「연결」뿐이다.

⚠️이 파일이 잡으려는 사고는 하나다 — `app/config.py` 의 Settings 는 dataclass
기본값이라 `os.getenv` 가 ★클래스 정의 시점에 한 번만 평가된다. 로더 호출을
`settings = Settings()` 바로 앞에 두면 ★이미 늦다. 그 배치는 아무 오류 없이
조용히 옛 값을 쓴다.
"""
import importlib
import sys

import app.config as config
import app.secrets_loader as secrets_loader


def test_설정을_읽으면_로더가_이미_돌아_있다():
    # 기본값(SECRETS_BACKEND 없음)에서는 「미사용」으로 끝나야 한다.
    result = secrets_loader.last_result()
    assert result is not None, "config 를 import 했는데 로더가 한 번도 안 돌았다"
    assert result.backend == "none"


def test_로더가_클래스_정의_전에_불린다(monkeypatch):
    """주입한 값이 Settings 기본값에 ★반영되는 자리인지 실제로 확인한다."""
    calls: list[str] = []

    def 가짜로더(environ=None):
        env = environ if environ is not None else __import__("os").environ
        env["APP_SECRET"] = "주입된-값"
        calls.append("불림")
        return secrets_loader.LoadResult(backend="가짜", loaded=["APP_SECRET"])

    monkeypatch.setattr(config, "load_secrets_into_env", 가짜로더)
    monkeypatch.setitem(sys.modules, "app.config", config)
    monkeypatch.setattr(
        sys.modules["app.secrets_loader"], "load_secrets_into_env", 가짜로더
    )

    다시 = importlib.reload(config)
    assert calls, "config 를 다시 읽었는데 로더가 안 불렸다"
    # ★핵심 — 주입한 값이 Settings 에 실제로 닿았는가.
    assert 다시.settings.app_secret == "주입된-값"

    # 다른 시험에 영향이 없도록 환경을 되돌리고 원래대로 다시 읽는다.
    __import__("os").environ.pop("APP_SECRET", None)
    monkeypatch.undo()
    importlib.reload(config)
