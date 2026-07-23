from __future__ import annotations

import os
import secrets
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
target = ROOT / ".env"
if target.exists():
    print(f"Configuration already exists: {target}")
    raise SystemExit(0)

content = f"""APP_ENV=production
APP_HOST=0.0.0.0
APP_PORT=8000
APP_SECRET={secrets.token_urlsafe(48)}
CAPTCHA_SITE_KEY=site_{secrets.token_urlsafe(24)}
CAPTCHA_SITE_SECRET=secret_{secrets.token_urlsafe(40)}
CAPTCHA_ADMIN_KEY=admin_{secrets.token_urlsafe(32)}
ALLOWED_ORIGINS=*
TRUST_PROXY=false

DB_NAME=captcha_ms
DB_USER=catchap_dba
DB_PASSWORD=
DB_UNIX_SOCKET=/var/run/mysqld/mysqld.sock
DB_HOST=127.0.0.1
DB_PORT=3306

IMAGE_DIR=data/processed
CHALLENGE_DIR=data/challenges
CHALLENGE_TTL_SECONDS=120
VERIFICATION_TTL_SECONDS=120
MAX_ATTEMPTS=3
MAX_CHALLENGES_PER_MINUTE=30
POSITION_TOLERANCE_PX=7
CAPTCHA_MODE=giraffe
GIRAFFE_DIR=data/giraffe_drinking
"""
target.write_text(content, encoding="utf-8")
os.chmod(target, 0o600)
print(f"Created secure configuration: {target}")
