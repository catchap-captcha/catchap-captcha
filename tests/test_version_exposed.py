"""배포가 파드까지 닿았는지 **바깥에서 한 번에** 알 수 있어야 한다 (2026-08-14).

이게 없어서 오진했다. 2026-08-14 에 문항 출제 순서 변경(catchap-captcha#41)이 반영
안 된 줄 알고 2시간을 배포 문제로 쫓았는데, 실제로는 **03:24:26 에 정상 배포됐고
코드가 안 듣는 것**이었다. 앱이 버전을 안 알려주니 동작으로 추정할 수밖에 없었고,
추정이 틀렸다.

⚠️`/health` 는 앱 것이 아니다 — 캡차 앱에 그 경로가 없어서 프론트엔드 HTML 이 200 으로
돌아온다. "200 이니 정상" 으로 읽으면 안 된다. 앱의 상태는 `/health/ready` 하나뿐이고,
그래서 버전도 거기 싣는다.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from app.config import settings

ROOT = Path(__file__).resolve().parent.parent


def test_설정이_커밋을_읽는다():
    """빌드할 때 심은 값을 그대로 읽어야 한다. 안 심겼으면 unknown."""
    assert settings.git_sha == os.getenv("GIT_SHA", "unknown")


def test_ready_응답에_버전이_실린다():
    """★빠지면 바깥에서 배포 여부를 확인할 방법이 다시 없어진다."""
    source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    ready = source[source.index('@app.get("/health/ready")'):]
    ready = ready[:ready.index("@app.get", 10)]
    assert '"version": settings.git_sha' in ready


def test_이미지가_커밋을_심는다():
    """Dockerfile 이 안 받으면 앱은 영원히 unknown 이다."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^ARG GIT_SHA", dockerfile, re.M)
    assert re.search(r"^ENV GIT_SHA=\$GIT_SHA", dockerfile, re.M)


def test_워크플로가_커밋을_넘긴다():
    """Dockerfile 이 받을 준비만 하고 워크플로가 안 넘기면 역시 unknown 이다.

    ★이 둘은 따로 떨어져 있어서 한쪽만 고치기 쉽다. 그래서 같이 본다.
    """
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    assert "build-args:" in workflow
    assert "GIT_SHA=${{ steps.tag.outputs.sha }}" in workflow
