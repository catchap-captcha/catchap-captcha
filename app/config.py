from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


ROOT_DIR = Path(__file__).resolve().parent.parent


def path_setting(name: str, default: str) -> Path:
    value = Path(os.getenv(name, default))
    return value if value.is_absolute() else ROOT_DIR / value


def _parse_admin_keys(raw: str) -> dict[str, str]:
    """'이름:키,이름:키' 형식을 {키: 이름} 매핑으로 파싱한다."""
    mapping: dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if ":" not in part:
            continue
        name, key = part.split(":", 1)
        name, key = name.strip(), key.strip()
        if name and key:
            mapping[key] = name
    return mapping


@dataclass(frozen=True)
class Settings:
    app_secret: str = os.getenv("APP_SECRET", "dev-only-change-me")
    site_key: str = os.getenv("CAPTCHA_SITE_KEY", "public-demo-key")
    site_secret: str = os.getenv("CAPTCHA_SITE_SECRET", "private-demo-secret")
    admin_key: str = os.getenv("CAPTCHA_ADMIN_KEY", "admin-demo-key")
    admin_keys: dict[str, str] = field(default_factory=lambda: _parse_admin_keys(os.getenv("CAPTCHA_ADMIN_KEYS", "")))
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
    challenge_ttl_seconds: int = int(os.getenv("CHALLENGE_TTL_SECONDS", "60"))
    verification_ttl_seconds: int = int(os.getenv("VERIFICATION_TTL_SECONDS", "300"))
    max_attempts: int = int(os.getenv("MAX_ATTEMPTS", "3"))
    max_challenges_per_minute: int = int(os.getenv("MAX_CHALLENGES_PER_MINUTE", "30"))
    rate_limit_per_minute: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "300"))
    behavior_step_up_score: int = int(os.getenv("BEHAVIOR_STEP_UP_SCORE", "30"))
    behavior_block_score: int = int(os.getenv("BEHAVIOR_BLOCK_SCORE", "80"))
    cluster_block_size: int = int(os.getenv("CLUSTER_BLOCK_SIZE", "7"))
    cluster_window_hours: int = int(os.getenv("CLUSTER_WINDOW_HOURS", "24"))
    rotation_cooldown_seconds: int = int(os.getenv("ROTATION_COOLDOWN_SECONDS", "300"))
    pow_enabled: bool = os.getenv("POW_ENABLED", "1") == "1"
    pow_difficulty_bits: int = int(os.getenv("POW_DIFFICULTY_BITS", "17"))
    # 적응형 PoW: 최근 실패/과다요청 세션엔 난이도를 올려 봇 재시도 비용을 계단식 상승.
    pow_stepup_bits: int = int(os.getenv("POW_STEPUP_BITS", "4"))
    pow_stepup_failures: int = int(os.getenv("POW_STEPUP_FAILURES", "1"))
    pow_stepup_challenges: int = int(os.getenv("POW_STEPUP_CHALLENGES", "5"))
    # 허니팟: 빈 영역에 심는 투명 함정 히트영역 수(사람은 안 건드림, 열거 봇만 집음).
    honeypot_count: int = int(os.getenv("HONEYPOT_COUNT", "1"))
    # 행동 AI(별도 내부 모델 서비스). URL/키가 비면 비활성 → 캡챠 판정에 영향 없음(A와 동일 안전장치).
    behavior_ai_url: str = os.getenv("BEHAVIOR_AI_URL", "")
    behavior_ai_backend_key: str = os.getenv("BEHAVIOR_AI_BACKEND_KEY", "")
    behavior_ai_timeout_seconds: float = float(os.getenv("BEHAVIOR_AI_TIMEOUT_SECONDS", "1.5"))
    # shadow가 기본. 실데이터로 모델·임계값이 보정될 때까지 캡챠 정답이 authoritative.
    behavior_policy_mode: Literal["shadow", "active"] = os.getenv("BEHAVIOR_POLICY_MODE", "shadow")  # type: ignore[assignment]
    behavior_debug_response: bool = os.getenv("BEHAVIOR_DEBUG_RESPONSE", "false").lower() == "true"
    # active 승격 go/no-go 기준: 사람 프록시(정답 통과) 표본 최소치 + 허용 오탐 프록시율.
    behavior_promote_min_passed: int = int(os.getenv("BEHAVIOR_PROMOTE_MIN_PASSED", "500"))
    behavior_promote_max_fp_rate: float = float(os.getenv("BEHAVIOR_PROMOTE_MAX_FP_RATE", "0.02"))
    final_dir: Path = path_setting("FINAL_DIR", "data/final")
    labeling_dir: Path = path_setting("LABELING_DIR", "data/labeling")
    runtime_dir: Path = path_setting("RUNTIME_DIR", "data/runtime")
    static_dir: Path = path_setting("STATIC_DIR", "static/dist")

    def _single_admin_ok(self) -> bool:
        v = self.admin_key
        return bool(v) and "demo" not in v and "change-me" not in v

    def reviewer_for_key(self, key: str | None) -> str | None:
        """관리자 키로 검수자 이름을 돌려준다. 유효하지 않으면 None."""
        if not key:
            return None
        if key in self.admin_keys:
            return self.admin_keys[key]
        if self._single_admin_ok() and key == self.admin_key:
            return "admin"
        return None

    def validate(self) -> None:
        if os.getenv("APP_ENV", "development") == "production":
            for name, value in {
                "APP_SECRET": self.app_secret,
                "CAPTCHA_SITE_SECRET": self.site_secret,
            }.items():
                if not value or "demo" in value or "change-me" in value:
                    raise RuntimeError(f"{name} must be configured for production")
            if not self.admin_keys and not self._single_admin_ok():
                raise RuntimeError("CAPTCHA_ADMIN_KEYS (또는 CAPTCHA_ADMIN_KEY) must be configured for production")


settings = Settings()
