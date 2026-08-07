#!/usr/bin/env python3
"""시크릿 로테이션 유틸리티.

캡차 앱 시크릿(관리자키 5개·CAPTCHA_ADMIN_KEY·CAPTCHA_SITE_SECRET·APP_SECRET)을
서버에서 새로 생성해 .env를 갱신한다. **DB 계정 비밀번호는 건드리지 않는다.**

값은 화면에 출력하지 않는다 — 새 관리자키만 배포용 파일(NEW_ADMIN_KEYS.txt)에 기록한다.

사용법:
    python3 scripts/rotate_secrets.py [/path/to/.env]
    # 또는  ROTATE_ENV_PATH=/path/to/.env python3 scripts/rotate_secrets.py
    # 인자/환경변수 없으면 현재 디렉터리의 ./.env 를 대상으로 한다.

실행 후 순서(락아웃 방지):
    1) 생성된 NEW_ADMIN_KEYS.txt 를 각 팀원에게 안전하게 전달(채팅/메신저 평문 지양)
    2) 인강 연동 담당에게 새 CAPTCHA_SITE_SECRET 전달(.env에서 읽기)
    3) **배포가 끝난 뒤** 서비스 재기동으로 활성화 → 기존 키 즉시 무효
       (재기동 전까지 기존 키가 유효하므로 락아웃 없음. 방화벽 개방 직전에 묶어서 하는 것을 권장)

⚠️ 생성물(NEW_ADMIN_KEYS.txt / .env / .env.pre-rotate)은 실제 비밀이다. .gitignore에 등록되어 있으니 절대 커밋하지 말 것.
"""
from __future__ import annotations

import os
import re
import secrets
import sys
from pathlib import Path

# 관리자 키를 발급받을 팀원 (필요 시 ADMIN_NAMES="a,b,c" 로 재정의)
ADMIN_NAMES = [n.strip() for n in os.environ.get("ADMIN_NAMES", "민서,태형,지영,성원,민용").split(",") if n.strip()]


def upsert(lines: list[str], key: str, val: str) -> list[str]:
    pat = re.compile(rf"^{re.escape(key)}=")
    for i, ln in enumerate(lines):
        if pat.match(ln):
            lines[i] = f"{key}={val}"
            return lines
    lines.append(f"{key}={val}")
    return lines


def main() -> int:
    env_path = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("ROTATE_ENV_PATH", ".env"))
    if not env_path.is_file():
        print(f"ERR: .env 파일 없음: {env_path}\n   경로를 인자/ROTATE_ENV_PATH로 지정하세요.", file=sys.stderr)
        return 1

    new_admin = {n: "cak_" + secrets.token_urlsafe(24) for n in ADMIN_NAMES}
    updates = {
        "CAPTCHA_ADMIN_KEYS": ",".join(f"{n}:{k}" for n, k in new_admin.items()),
        "CAPTCHA_ADMIN_KEY": "cak_" + secrets.token_urlsafe(24),   # 단일 폴백도 새로
        "CAPTCHA_SITE_SECRET": "css_" + secrets.token_urlsafe(32),
        "APP_SECRET": secrets.token_urlsafe(32),
    }

    # 백업
    backup = env_path.with_name(env_path.name + ".pre-rotate")
    backup.write_text(env_path.read_text())
    os.chmod(backup, 0o600)

    lines = env_path.read_text().splitlines()
    for key, val in updates.items():
        lines = upsert(lines, key, val)
    env_path.write_text("\n".join(lines) + "\n")

    dist = env_path.with_name("NEW_ADMIN_KEYS.txt")
    dist.write_text("새 관리자 키 (각 팀원에게 안전하게 전달 후 이 파일 삭제)\n\n" +
                    "\n".join(f"{n}: {k}" for n, k in new_admin.items()) + "\n")
    os.chmod(dist, 0o600)

    print(f"✅ 로테이션 완료 — {env_path} 갱신 (백업: {backup.name})")
    print(f"• 관리자키 {len(new_admin)}개 → {dist} (읽어서 팀 배포 후 삭제)")
    print("• 새 CAPTCHA_SITE_SECRET·APP_SECRET → .env 안에 있음(값 미출력)")
    print("• 활성화: 배포 후 서비스 재기동. ⚠️ 재기동 시 기존 키 즉시 무효 — 배포 완료 후 재기동 권장.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
