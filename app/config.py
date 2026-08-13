from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .secrets_loader import load_secrets_into_env

# ★Settings 클래스를 만들기 ★전에 금고를 읽어 os.environ 에 넣는다.
#   이 파일의 Settings 는 dataclass 기본값이라 os.getenv 가 ★클래스 정의 시점에
#   한 번 평가된다. 그래서 settings = Settings() 앞이 아니라 ★class 문 앞이어야 한다.
#   (백엔드는 pydantic 이라 get_settings() 안에서 부르지만, 여기는 자리가 다르다.)
# SECRETS_BACKEND 기본값이 none 이라 로컬 개발·시험에서는 아무 일도 일어나지 않는다.
load_secrets_into_env()


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
    # 캡차를 iframe으로 임베드해도 되는 출처 허용목록. CSP frame-ancestors(②)와
    # 프론트 postMessage targetOrigin 검증(③)에 함께 쓰인다. 미설정 시 self만 허용.
    embed_origins: tuple[str, ...] = tuple(
        part.strip() for part in os.getenv("EMBED_ORIGINS", "").split(",") if part.strip()
    )
    trust_proxy: bool = os.getenv("TRUST_PROXY", "false").lower() == "true"
    # ★★스키마를 앱이 만들지 않는다 (2026-08-10).
    #
    #   지금까지 이 앱은 기동할 때마다 CREATE TABLE IF NOT EXISTS 를 14번,
    #   멱등 ALTER 를 6번 했다. 그래서 앱 계정(catchap_captcha_app)에
    #   ★CREATE·ALTER·INDEX·REFERENCES 권한을 줘야 했다.
    #   = 캡차 파드가 뚫리면 ★표를 지우거나 바꿀 수 있다는 뜻이다.
    #
    #   true 면 기동할 때 DDL 을 ★안 하고, 대신 ★있어야 할 표·칼럼이 다 있는지
    #   ★확인만 한다. 없으면 기동을 막는다 — 조용히 뜬 뒤 첫 요청에서
    #   "그런 표 없음" 이 나면 원인이 멀어지기 때문이다.
    #
    #   ★기본값 false = 지금까지와 똑같이 동작한다(로컬 개발·시험).
    schema_managed_externally: bool = os.getenv("SCHEMA_MANAGED_EXTERNALLY", "false").lower() == "true"
    db_name: str = os.getenv("DB_NAME", "captcha_ms")
    # ⚠️★기본값을 "catchap_dba" 에서 ★빈 값으로 바꿨다 (0810).
    #   그 계정은 세 DB 에 ALL PRIVILEGES 가 있는 ★사람용 스키마 변경 계정이다.
    #   0810 에 DB_USER 가 ConfigMap 에서 금고로 옮겨 가면서, 금고에서 그 키만
    #   빠지면 이 기본값에 ★실제로 닿을 수 있게 됐다(전에는 ConfigMap 이 항상 채웠다).
    #   빈 값이면 접속이 곧바로 실패해 파드가 준비 안 됨으로 남는다 — 그게 옳은 실패다.
    db_user: str = os.getenv("DB_USER", "")
    db_password: str = os.getenv("DB_PASSWORD", "")
    db_unix_socket: str = os.getenv("DB_UNIX_SOCKET", "/var/run/mysqld/mysqld.sock")
    db_host: str = os.getenv("DB_HOST", "127.0.0.1")
    db_port: int = int(os.getenv("DB_PORT", "3306"))
    challenge_ttl_seconds: int = int(os.getenv("CHALLENGE_TTL_SECONDS", "60"))
    verification_ttl_seconds: int = int(os.getenv("VERIFICATION_TTL_SECONDS", "300"))
    max_attempts: int = int(os.getenv("MAX_ATTEMPTS", "3"))
    max_challenges_per_minute: int = int(os.getenv("MAX_CHALLENGES_PER_MINUTE", "30"))
    max_telemetry_failures_10m: int = int(os.getenv("MAX_TELEMETRY_FAILURES_10M", "3"))
    rate_limit_per_minute: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "300"))
    behavior_step_up_score: int = int(os.getenv("BEHAVIOR_STEP_UP_SCORE", "30"))
    behavior_block_score: int = int(os.getenv("BEHAVIOR_BLOCK_SCORE", "80"))
    cluster_block_size: int = int(os.getenv("CLUSTER_BLOCK_SIZE", "7"))
    cluster_window_hours: int = int(os.getenv("CLUSTER_WINDOW_HOURS", "24"))
    rotation_cooldown_seconds: int = int(os.getenv("ROTATION_COOLDOWN_SECONDS", "1800"))
    pow_enabled: bool = os.getenv("POW_ENABLED", "1") == "1"
    pow_difficulty_bits: int = int(os.getenv("POW_DIFFICULTY_BITS", "17"))
    # 적응형 PoW: 최근 실패/과다요청 세션엔 난이도를 올려 봇 재시도 비용을 계단식 상승.
    pow_stepup_bits: int = int(os.getenv("POW_STEPUP_BITS", "4"))
    # ── 계속 틀릴 때: 의심 여부로 대응을 나눈다 (2026-08-13)
    #
    # 틀림과 의심은 서로 다른 봇을 가리킨다. 틀리는 쪽은 문제를 못 푸는 봇(찍기)이고,
    # 의심되는 쪽은 문제는 푸는데 궤적이 기계인 봇이다.
    #
    #   의심 없음  → 대기 시간. 사람일 가능성이 높으니 CPU·배터리를 태우지 않는다.
    #   의심 있음  → PoW 상향. 기다리는 건 봇에게 싼 벌이다(그동안 다른 세션을 병렬로
    #                돌리면 그만) — 계산은 실제로 그 기기가 해야 한다.
    #
    # 실측 근거(0812~13): 사람 오답률 12.8% → 3연속 오답 0.2%. 찍는 봇은 오답률 75%
    # (문항 평균 정답 확률 25%) → 3연속 오답 42%. 3연속 오답은 봇이 200배 잘 낸다.
    # 그래서 사람 쪽 대기는 4회째부터 시작해 정상 사용자를 거의 건드리지 않는다.
    pow_max_bits: int = int(os.getenv("POW_MAX_BITS", "24"))
    # 오답 n회에서 시작하는 대기(초). "4회째 5초, 5회째 20초, 그 뒤 60초 상한".
    # 상한을 두는 이유 — 무한정 늘리면 결국 잠금과 같아지고, 오탐이 있는 한(특정
    # 참가자 14.1%) 성실한 사람이 못 들어간다. 맞히면 즉시 0으로 돌아간다.
    retry_delays: str = os.getenv("RETRY_DELAYS", "4:5;5:20;6:60")
    # ── 의심 세션이 계속 틀릴 때: 잠시 막는다 (2026-08-13, sw 요청)
    #
    # 의심(medium/high)이 붙은 채로 이 횟수만큼 틀리면 새 문제를 안 준다.
    #
    # ⚠️정직하게 — 이 차단은 세션 단위이고, 세션 ID 는 브라우저가 직접 만든다
    #   (`guard-<시각>-<난수>`). 새로 만들면 풀린다. 즉 제대로 만든 봇은 안 걸리고
    #   그냥 다시 시도하는 부류만 걸린다. IP 로 걸면 같은 공유기를 쓰는 사람이 전부
    #   걸리므로 그쪽이 더 나쁘다.
    #
    #   비용 부담은 PoW 가 진짜로 진다 — 세션을 바꿔도 계산은 다시 해야 한다.
    #   이 차단은 그 위에 얹는 값싼 한 겹이다.
    #
    # ★시간 제한을 두는 이유 — 우리 판정은 틀린다(특정 참가자 오탐 14.1%). 영구
    #   차단이면 그 사람이 로그인을 못 한다. 기다리면 풀리게 둔다.
    suspicious_block_failures: int = int(os.getenv("SUSPICIOUS_BLOCK_FAILURES", "5"))
    suspicious_block_seconds: int = int(os.getenv("SUSPICIOUS_BLOCK_SECONDS", "300"))
    pow_stepup_failures: int = int(os.getenv("POW_STEPUP_FAILURES", "1"))
    pow_stepup_challenges: int = int(os.getenv("POW_STEPUP_CHALLENGES", "5"))
    # 허니팟: 빈 영역에 심는 투명 함정 히트영역 수(사람은 안 건드림, 열거 봇만 집음).
    honeypot_count: int = int(os.getenv("HONEYPOT_COUNT", "1"))
    # step-up 계층(sw 요청): 의심 세션에 다음 챌린지를 어렵게(객체 수·PoW 비트·허니팟 상향).
    # ⚠️ 기본 OFF — AI 채점이 드래그 단위로 바뀌기 전 켜면 '규모 클수록 사람 판정'이라 제일 의심스러운
    #    세션에 제일 관대해짐(sw 지적). per-drag 채점(sw 4094fbf)과 함께 켠다.
    step_up_enabled: bool = os.getenv("STEP_UP_ENABLED", "0") == "1"
    # 계층별 "min객체,max객체,PoW비트,허니팟수" 세미콜론 구분(1→3단계). 실객체(invalid 제외) 기준.
    step_up_tiers: str = os.getenv("STEP_UP_TIERS", "2,3,17,1;4,5,19,2;6,8,21,2")
    # 세션 누적 챌린지(=재도전=의심 신호) 임계로 승급. 1st→1단계, tier2_at→2단계, tier3_at→3단계.
    step_up_tier2_at: int = int(os.getenv("STEP_UP_TIER2_AT", "1"))
    step_up_tier3_at: int = int(os.getenv("STEP_UP_TIER3_AT", "2"))
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
    # Local-only diagnostics. Keep this disabled outside a developer-run test
    # because behavior scores must not be exposed to CAPTCHA clients.
    behavior_debug_response: bool = os.getenv("BEHAVIOR_DEBUG_RESPONSE", "false").lower() == "true"
    # active 승격 go/no-go 기준: 사람 프록시(정답 통과) 표본 최소치 + 허용 오탐 프록시율.
    behavior_promote_min_passed: int = int(os.getenv("BEHAVIOR_PROMOTE_MIN_PASSED", "500"))
    behavior_promote_max_fp_rate: float = float(os.getenv("BEHAVIOR_PROMOTE_MAX_FP_RATE", "0.02"))
    # ── 만료 토큰 정리 ──────────────────────────────────────────────
    # 토큰 수명은 몇 분인데 지우는 곳이 없어 만료된 것이 그대로 쌓인다
    # (0813 기준 2,991행 · 전부 만료). ★참조하는 표가 없어 지워도 안전하다.
    # 여유를 두는 이유 — 서버 시계가 조금 어긋나도 살아 있는 토큰을 안 건드리게.
    token_retention_hours: int = int(os.getenv("CAPTCHA_TOKEN_RETENTION_HOURS", "24"))
    # 한 번에 지우는 최대 행수. 크게 잡으면 잠금이 길어진다.
    token_purge_batch: int = int(os.getenv("CAPTCHA_TOKEN_PURGE_BATCH", "1000"))

    # ── Valkey(캐시) ────────────────────────────────────────────────
    # ★기본값은 전부 "안 쓴다" 이다. VALKEY_HOST 가 비어 있으면 코드가 캐시를
    #   아예 건드리지 않고 지금까지처럼 DB 만 쓴다 — 로컬 개발·시험이 그대로 돈다.
    # ⚠️캐시가 죽으면 ★DB 로 되돌아간다. "캐시가 죽으면 통과" 는 하지 않는다 —
    #   캐시를 죽이는 것이 곧 레이트리밋 우회가 되기 때문이다.
    valkey_host: str = os.getenv("VALKEY_HOST", "")
    valkey_port: int = int(os.getenv("VALKEY_PORT", "6379"))
    valkey_username: str = os.getenv("VALKEY_USERNAME", "")
    valkey_password: str = os.getenv("VALKEY_PASSWORD", "")
    valkey_tls: bool = os.getenv("VALKEY_TLS", "false").lower() == "true"
    valkey_tls_ca_file: str = os.getenv("VALKEY_TLS_CA_FILE", "")
    # ★인증서에 SAN 이 없어 엔드포인트가 아니라 CN 을 줘야 한다. 자세한 것은 cache.py.
    valkey_tls_server_name: str = os.getenv("VALKEY_TLS_SERVER_NAME", "")
    valkey_key_prefix: str = os.getenv("VALKEY_KEY_PREFIX", "cc:")
    valkey_timeout_seconds: float = float(os.getenv("VALKEY_TIMEOUT_SECONDS", "0.3"))
    # ★연결에 실패하면 이만큼 쉬었다가 다시 시도한다. 안 쉬면 캐시가 죽었을 때
    #   요청마다 연결을 다시 걸어 ★캐시 없을 때보다 느려진다.
    valkey_retry_seconds: float = float(os.getenv("VALKEY_RETRY_SECONDS", "10"))
    # 1단계는 ★캐시와 DB 를 같이 돌려 값을 비교만 한다. 판정은 여전히 DB 값으로 한다.
    #   compare  둘 다 세고 다르면 로그만 남긴다 (기본)
    #   cache    캐시 값으로 판정한다 (비교가 충분히 쌓인 뒤에 켠다)
    valkey_rate_limit_mode: Literal["off", "compare", "cache"] = os.getenv(
        "VALKEY_RATE_LIMIT_MODE", "compare")  # type: ignore[assignment]

    final_dir: Path = path_setting("FINAL_DIR", "data/final")
    # ★문항 자산(이미지·조각·매니페스트)을 어디에 두는가 — app/asset_storage.py 참고.
    #   local  : 지금까지와 같은 로컬 디스크(final_dir). 개발·시험의 기본값.
    #   object : 오브젝트 스토리지. 운영에서 쓴다 — 이미지에 367MB 를 굽지 않기 위해서다.
    #   ★백엔드의 MEDIA_* 와 같은 모양으로 맞췄다(다른 방식을 새로 만들지 않는다).
    asset_storage_backend: Literal["local", "object"] = os.getenv("ASSET_STORAGE_BACKEND", "local")  # type: ignore[assignment]
    asset_bucket: str = os.getenv("ASSET_BUCKET", "")
    # 버킷 안 최상위 접두사. ★백엔드 미디어(media/)와 겹치지 않게 따로 둔다.
    asset_key_prefix: str = os.getenv("ASSET_KEY_PREFIX", "captcha-service/final")
    asset_s3_endpoint: str = os.getenv("ASSET_S3_ENDPOINT", "https://objectstorage.kr-central-2.kakaocloud.com")
    asset_s3_region: str = os.getenv("ASSET_S3_REGION", "kr-central-2")
    asset_s3_access_key: str = os.getenv("ASSET_S3_ACCESS_KEY", "")
    asset_s3_secret_key: str = os.getenv("ASSET_S3_SECRET_KEY", "")
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
        if self.behavior_policy_mode not in {"shadow", "active"}:
            raise RuntimeError("BEHAVIOR_POLICY_MODE must be shadow or active")
        if self.behavior_event_transport not in {"off", "shadow", "active"}:
            raise RuntimeError("BEHAVIOR_EVENT_TRANSPORT must be off, shadow or active")
        if self.valkey_rate_limit_mode not in {"off", "compare", "cache"}:
            raise RuntimeError("VALKEY_RATE_LIMIT_MODE must be off, compare or cache")
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
