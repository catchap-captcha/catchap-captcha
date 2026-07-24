from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent


def path_setting(name: str, default: str) -> Path:
    value = Path(os.getenv(name, default))
    return value if value.is_absolute() else ROOT_DIR / value


@dataclass(frozen=True)
class Settings:
    app_secret: str = os.getenv("APP_SECRET", "dev-only-change-me")
    site_key: str = os.getenv("CAPTCHA_SITE_KEY", "public-demo-key")
    site_secret: str = os.getenv("CAPTCHA_SITE_SECRET", "private-demo-secret")
    admin_key: str = os.getenv("CAPTCHA_ADMIN_KEY", "admin-demo-key")
    allowed_origins: tuple[str, ...] = tuple(
        part.strip() for part in os.getenv("ALLOWED_ORIGINS", "*").split(",") if part.strip()
    )
    trust_proxy: bool = os.getenv("TRUST_PROXY", "false").lower() == "true"
    db_name: str = os.getenv("DB_NAME", "captcha_ms")
    db_user: str = os.getenv("DB_USER", "catchap_dba")
    db_password: str = os.getenv("DB_PASSWORD", "")
    db_unix_socket: str = os.getenv("DB_UNIX_SOCKET", "/var/run/mysqld/mysqld.sock")
    db_host: str = os.getenv("DB_HOST", "127.0.0.1")
    db_port: int = int(os.getenv("DB_PORT", "3306"))
    challenge_ttl_seconds: int = int(os.getenv("CHALLENGE_TTL_SECONDS", "180"))
    verification_ttl_seconds: int = int(os.getenv("VERIFICATION_TTL_SECONDS", "300"))
    max_attempts: int = int(os.getenv("MAX_ATTEMPTS", "3"))
    max_challenges_per_minute: int = int(os.getenv("MAX_CHALLENGES_PER_MINUTE", "30"))
    # The behavior model runs as a separate internal service. Browser clients
    # never receive this key and cannot call the model directly.
    behavior_ai_url: str = os.getenv("BEHAVIOR_AI_URL", "")
    behavior_ai_backend_key: str = os.getenv("BEHAVIOR_AI_BACKEND_KEY", "")
    behavior_ai_timeout_seconds: float = float(os.getenv("BEHAVIOR_AI_TIMEOUT_SECONDS", "1.5"))
    # Shadow is deliberately the default. A CAPTCHA answer stays authoritative
    # until real main-CAPTCHA data has calibrated the model and thresholds.
    behavior_policy_mode: Literal["shadow", "active"] = os.getenv("BEHAVIOR_POLICY_MODE", "shadow")  # type: ignore[assignment]
    # This controls how browser events reach the CAPTCHA server. Keep it off
    # until the production CAPTCHA frontend and DB are deployed together.
    behavior_event_transport: Literal["off", "shadow", "active"] = os.getenv("BEHAVIOR_EVENT_TRANSPORT", "off")  # type: ignore[assignment]
    final_dir: Path = path_setting("FINAL_DIR", "data/final")
    labeling_dir: Path = path_setting("LABELING_DIR", "data/labeling")
    runtime_dir: Path = path_setting("RUNTIME_DIR", "data/runtime")
    static_dir: Path = path_setting("STATIC_DIR", "static/dist")

    def validate(self) -> None:
        if self.behavior_policy_mode not in {"shadow", "active"}:
            raise RuntimeError("BEHAVIOR_POLICY_MODE must be shadow or active")
        if self.behavior_event_transport not in {"off", "shadow", "active"}:
            raise RuntimeError("BEHAVIOR_EVENT_TRANSPORT must be off, shadow or active")
        if os.getenv("APP_ENV", "development") == "production":
            for name, value in {
                "APP_SECRET": self.app_secret,
                "CAPTCHA_SITE_SECRET": self.site_secret,
                "CAPTCHA_ADMIN_KEY": self.admin_key,
            }.items():
                if not value or "demo" in value or "change-me" in value:
                    raise RuntimeError(f"{name} must be configured for production")


settings = Settings()
