from __future__ import annotations

import hashlib
import hmac
import json
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import pymysql
from pymysql.cursors import DictCursor

from .cache import Cache, compare_and_log
from .cache import KEY_SHAPE as _KEY_SHAPE
from .config import Settings


SCHEMA = [
    """CREATE TABLE IF NOT EXISTS captcha_questions (
      id VARCHAR(64) PRIMARY KEY, type VARCHAR(32) NOT NULL,
      instruction_ko VARCHAR(500) NOT NULL, instruction_en VARCHAR(500) NULL,
      source VARCHAR(64) NOT NULL, source_question_id VARCHAR(128) NULL,
      image_path VARCHAR(500) NOT NULL, image_width INT UNSIGNED NOT NULL,
      image_height INT UNSIGNED NOT NULL, difficulty TINYINT UNSIGNED NOT NULL DEFAULT 2,
      status VARCHAR(24) NOT NULL DEFAULT 'draft', review_status VARCHAR(24) NOT NULL DEFAULT 'pending',
      reviewer VARCHAR(128) NULL, reviewed_at DATETIME(6) NULL, created_at DATETIME(6) NOT NULL,
      INDEX idx_question_status(status, review_status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS captcha_objects (
      id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, question_id VARCHAR(64) NOT NULL,
      object_key VARCHAR(128) NOT NULL, label VARCHAR(128) NOT NULL,
      bbox_x DOUBLE NOT NULL, bbox_y DOUBLE NOT NULL, bbox_width DOUBLE NOT NULL, bbox_height DOUBLE NOT NULL,
      role VARCHAR(16) NOT NULL, piece_path VARCHAR(500) NULL,
      UNIQUE KEY uq_question_object(question_id, object_key), INDEX idx_object_question(question_id),
      CONSTRAINT fk_object_question FOREIGN KEY(question_id) REFERENCES captcha_questions(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS captcha_challenges_v2 (
      id CHAR(36) PRIMARY KEY, question_id VARCHAR(64) NOT NULL, session_id VARCHAR(128) NOT NULL,
      purpose VARCHAR(32) NOT NULL, lecture_id VARCHAR(128) NULL, expires_at DATETIME(6) NOT NULL,
      attempt_count TINYINT UNSIGNED NOT NULL DEFAULT 0, status VARCHAR(16) NOT NULL DEFAULT 'issued',
      created_at DATETIME(6) NOT NULL, verified_at DATETIME(6) NULL,
      client_ip_hash CHAR(64) NOT NULL, INDEX idx_challenge_expiry(expires_at),
      INDEX idx_challenge_rate(client_ip_hash, created_at),
      CONSTRAINT fk_challenge_question FOREIGN KEY(question_id) REFERENCES captcha_questions(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS captcha_challenge_objects (
      challenge_id CHAR(36) NOT NULL, object_id BIGINT UNSIGNED NOT NULL,
      temporary_object_id VARCHAR(64) NOT NULL,
      PRIMARY KEY(challenge_id, temporary_object_id), UNIQUE KEY uq_challenge_object(challenge_id, object_id),
      CONSTRAINT fk_map_challenge FOREIGN KEY(challenge_id) REFERENCES captcha_challenges_v2(id) ON DELETE CASCADE,
      CONSTRAINT fk_map_object FOREIGN KEY(object_id) REFERENCES captcha_objects(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS captcha_behavior_sessions (
      challenge_id CHAR(36) PRIMARY KEY, nonce_hash CHAR(64) NOT NULL,
      next_batch_seq INT UNSIGNED NOT NULL DEFAULT 0, last_receipt_hash CHAR(64) NULL,
      received_event_count INT UNSIGNED NOT NULL DEFAULT 0, created_at DATETIME(6) NOT NULL,
      CONSTRAINT fk_behavior_session_challenge FOREIGN KEY(challenge_id)
        REFERENCES captcha_challenges_v2(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS captcha_behavior_batches (
      id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, challenge_id CHAR(36) NOT NULL,
      batch_seq INT UNSIGNED NOT NULL, event_count SMALLINT UNSIGNED NOT NULL,
      previous_receipt_hash CHAR(64) NULL, payload_hash CHAR(64) NOT NULL,
      receipt_hash CHAR(64) NOT NULL, events_json JSON NOT NULL,
      received_at DATETIME(6) NOT NULL,
      UNIQUE KEY uq_behavior_batch(challenge_id, batch_seq),
      INDEX idx_behavior_batch_challenge(challenge_id, batch_seq),
      CONSTRAINT fk_behavior_batch_challenge FOREIGN KEY(challenge_id)
        REFERENCES captcha_challenges_v2(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS captcha_attempts (
      id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, challenge_id CHAR(36) NOT NULL,
      selected_object_ids JSON NOT NULL, is_correct BOOLEAN NOT NULL,
      failure_reason VARCHAR(64) NULL, duration_ms INT UNSIGNED NOT NULL,
      behavior_summary JSON NULL, raw_event_path VARCHAR(500) NULL, created_at DATETIME(6) NOT NULL,
      INDEX idx_attempt_challenge(challenge_id),
      CONSTRAINT fk_attempt_challenge FOREIGN KEY(challenge_id) REFERENCES captcha_challenges_v2(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS behavior_summaries (
      attempt_id BIGINT UNSIGNED PRIMARY KEY, reaction_time_ms INT UNSIGNED NULL,
      total_duration_ms INT UNSIGNED NOT NULL, drag_count INT UNSIGNED NOT NULL,
      wrong_object_count INT UNSIGNED NOT NULL, average_speed DOUBLE NOT NULL,
      speed_variance DOUBLE NOT NULL, path_length DOUBLE NOT NULL,
      path_curvature DOUBLE NOT NULL, pause_count INT UNSIGNED NOT NULL,
      CONSTRAINT fk_summary_attempt FOREIGN KEY(attempt_id) REFERENCES captcha_attempts(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS behavior_shadow_predictions (
      captcha_attempt_id BIGINT UNSIGNED PRIMARY KEY,
      behavior_attempt_id VARCHAR(64) NOT NULL UNIQUE,
      status VARCHAR(16) NOT NULL, detail VARCHAR(500) NULL,
      local_policy_mode VARCHAR(16) NOT NULL, model_policy_mode VARCHAR(16) NULL,
      risk_score DOUBLE NULL, risk_level VARCHAR(16) NULL, recommended_action VARCHAR(32) NULL,
      human_score DOUBLE NULL, bot_risk_score DOUBLE NULL,
      model_name VARCHAR(128) NULL, model_version VARCHAR(128) NULL,
      feature_schema_version VARCHAR(32) NULL, reasons JSON NULL,
      main_captcha_verdict VARCHAR(16) NOT NULL, final_verdict VARCHAR(16) NOT NULL,
      created_at DATETIME(6) NOT NULL,
      CONSTRAINT fk_shadow_prediction_attempt FOREIGN KEY(captcha_attempt_id)
        REFERENCES captcha_attempts(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS captcha_tokens (
      id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, challenge_id CHAR(36) NOT NULL,
      token_hash CHAR(64) NOT NULL UNIQUE, purpose VARCHAR(32) NOT NULL, lecture_id VARCHAR(128) NULL,
      session_id VARCHAR(128) NOT NULL, expires_at DATETIME(6) NOT NULL, consumed_at DATETIME(6) NULL,
      created_at DATETIME(6) NOT NULL,
      CONSTRAINT fk_token_challenge FOREIGN KEY(challenge_id) REFERENCES captcha_challenges_v2(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS captcha_users (
      id CHAR(36) PRIMARY KEY, email VARCHAR(320) NOT NULL UNIQUE,
      password_hash VARCHAR(255) NOT NULL, created_at DATETIME(6) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS captcha_review_claims (
      queue_id VARCHAR(128) PRIMARY KEY, reviewer_id VARCHAR(128) NOT NULL,
      claimed_at DATETIME(6) NOT NULL, expires_at DATETIME(6) NOT NULL,
      INDEX idx_review_claim_reviewer(reviewer_id), INDEX idx_review_claim_expiry(expires_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS captcha_review_decisions (
      queue_id VARCHAR(160) PRIMARY KEY, review_status VARCHAR(24) NOT NULL,
      reviewer VARCHAR(128) NULL, question_id VARCHAR(64) NULL, reviewed_at DATETIME(6) NOT NULL,
      INDEX idx_review_decision_status(review_status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS captcha_behavior_fingerprints (
      id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, session_id VARCHAR(128) NOT NULL,
      signature CHAR(16) NOT NULL, risk_score INT NOT NULL, created_at DATETIME(6) NOT NULL,
      INDEX idx_fp_sig(signature, created_at), INDEX idx_fp_created(created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
]


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json_bytes(value: Any) -> bytes:
    """Return a stable representation used for server-side batch receipts."""
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


# 좌표 정밀도. 저장·재읽기를 왕복해도 배치 해시가 같아야 하는데, 17자리 float 은
# JSON 컬럼을 거치면서 마지막 자리가 흔들려 payload_hash 재검증이 깨진다(실측:
# 원본 정밀도 → behavior_batch_payload_invalid, 반올림 → 통과). 6자리면 500px
# 이미지에서 0.0005px 이라 판별에 쓰는 정밀도보다 한참 아래다.
_COORD_PRECISION = 6

# PointerEvent 원천 신호 중 float 인 것들. 좌표와 같은 이유로 반올림이 필요하다 —
# 하나라도 빠지면 b04c315 와 똑같이 배치 해시 재검증이 조용히 깨지고, 그러면
# 모델은 호출조차 되지 않는데 캡차는 정상 통과해서 증상이 안 보인다.
#
# 자릿수를 필드마다 다르게 둔 이유:
#   pressure          0..1 이라 좌표와 같은 정밀도면 충분
#   pointer_width/height  px 단위(터치 접촉 폭). 소수점 3자리면 넘치게 충분
#   event_timestamp   페이지 기준 고해상 ms. 마이크로초까지 남기면 타이밍 분석에
#                     쓸 만하면서 왕복은 안정적이다
_FLOAT_PRECISION = {
    "x": _COORD_PRECISION,
    "y": _COORD_PRECISION,
    "pressure": _COORD_PRECISION,
    "pointer_width": 3,
    "pointer_height": 3,
    "event_timestamp": 3,
}


def _canonical_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """해시·저장에 쓸 정규형. float 필드를 고정 자릿수로 낮춰 왕복을 안정시킨다.

    저장 시점과 검증 시점에 **같은 함수**를 통과시키는 것이 요점이다. 저장 계층이
    자릿수 아래로 값을 흔들어도 두 해시가 갈라지지 않는다.

    null 표현은 건드리지 않는다. 프론트가 미지원 필드를 항상 명시적 null 로 보내고
    (``event?.X ?? null``) 서버 모델의 기본값도 None 이라, ``model_dump()`` 결과에
    키가 항상 존재한다. 생략과 null 이 섞이지 않으므로 양쪽 canonical 이 같다.
    """
    canonical: list[dict[str, Any]] = []
    for event in events:
        row = dict(event)
        for field, digits in _FLOAT_PRECISION.items():
            value = row.get(field)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                row[field] = round(float(value), digits)
        canonical.append(row)
    return canonical


def _payload_hash(events: list[dict[str, Any]]) -> str:
    return hashlib.sha256(_json_bytes(_canonical_events(events))).hexdigest()


def _receipt_timestamp(value: datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _receipt_hash(
    challenge_id: str,
    batch_seq: int,
    previous_receipt_hash: str | None,
    payload_hash: str,
    received_at: datetime,
) -> str:
    material = "|".join(
        (challenge_id, str(batch_seq), previous_receipt_hash or "", payload_hash, _receipt_timestamp(received_at))
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _decode_json(value: Any) -> Any:
    if isinstance(value, (str, bytes, bytearray)):
        return json.loads(value)
    return value


def _validate_behavior_batches(
    challenge_id: str,
    session: dict[str, Any],
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str | None]:
    """Validate the persisted receipt chain without trusting browser input."""
    expected_previous: str | None = None
    events: list[dict[str, Any]] = []
    expected_event_seq = 0
    for expected_seq, row in enumerate(rows):
        if int(row["batch_seq"]) != expected_seq:
            return [], "behavior_batch_sequence_invalid"
        batch_events = _decode_json(row["events_json"])
        if not isinstance(batch_events, list) or len(batch_events) != int(row["event_count"]):
            return [], "behavior_batch_event_count_invalid"
        for offset, event in enumerate(batch_events):
            if not isinstance(event, dict) or int(event.get("seq", -1)) != expected_event_seq + offset:
                return [], "behavior_event_sequence_invalid"
        if not hmac.compare_digest(_payload_hash(batch_events), row["payload_hash"]):
            return [], "behavior_batch_payload_invalid"
        if not hmac.compare_digest(row["previous_receipt_hash"] or "", expected_previous or ""):
            return [], "behavior_receipt_chain_invalid"
        expected_receipt = _receipt_hash(
            challenge_id,
            expected_seq,
            expected_previous,
            row["payload_hash"],
            row["received_at"],
        )
        if not hmac.compare_digest(expected_receipt, row["receipt_hash"]):
            return [], "behavior_receipt_invalid"
        expected_previous = row["receipt_hash"]
        events.extend(batch_events)
        expected_event_seq += len(batch_events)

    if int(session["next_batch_seq"]) != len(rows):
        return [], "behavior_batch_count_invalid"
    if not hmac.compare_digest(session["last_receipt_hash"] or "", expected_previous or ""):
        return [], "behavior_session_receipt_invalid"
    if int(session["received_event_count"]) != expected_event_seq:
        return [], "behavior_session_event_count_invalid"
    if not events:
        return [], "behavior_batches_missing"
    return events, None


# ★캐시가 세는 카운터 — request_pattern 의 반환값 중 이 넷만 캐시로 대체된다.
CACHED_COUNTERS = tuple(_KEY_SHAPE)


class Database:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        # ★캐시는 '있으면 쓰는 것'이다. 없거나 죽어도 아래 질의가 그대로 돈다.
        self.cache = Cache(settings)
        # 조각 총수는 거의 안 변한다(문항을 새로 넣을 때만). 요청마다 세면 낭비다.
        self._piece_total: int | None = None

    def _connect(self, autocommit: bool = False) -> pymysql.Connection:
        kwargs: dict[str, Any] = dict(
            user=self.settings.db_user, password=self.settings.db_password,
            database=self.settings.db_name, charset="utf8mb4", cursorclass=DictCursor,
            autocommit=autocommit, connect_timeout=5,
        )
        socket_path = Path(self.settings.db_unix_socket)
        local_hosts={"localhost","127.0.0.1","::1"}
        if self.settings.db_host in local_hosts and socket_path.exists(): kwargs["unix_socket"] = str(socket_path)
        else: kwargs.update(host=self.settings.db_host, port=self.settings.db_port)
        return pymysql.connect(**kwargs)

    @contextmanager
    def connection(self, autocommit: bool = False) -> Iterator[pymysql.Connection]:
        conn = self._connect(autocommit)
        try:
            yield conn
            if not autocommit: conn.commit()   # 정상 종료 시 자동 커밋(쓰기 누락 함정 제거)
        except Exception:
            if not autocommit: conn.rollback()
            raise
        finally:
            conn.close()

    # ★기동할 때 있어야 하는 것 — schema_managed_externally 일 때 ★확인만 한다.
    #   SCHEMA 에서 표 이름과 ★칼럼을 뽑아 쓰므로, 표든 칼럼이든 늘면 ★자동으로 같이 늘어난다.
    #
    # ⚠️아래 목록은 ★CREATE TABLE 에는 없고 ALTER 로만 추가되는 칼럼만 적는다.
    #   (initialize() 의 멱등 ALTER 목록과 짝을 이룬다)
    #   ★CREATE TABLE 안에 있는 칼럼은 여기 적을 필요가 없다 — 자동으로 잡힌다.
    #
    # ★0813 이전에는 이 손 목록 6개만 봤다. SCHEMA 의 칼럼 118개는 아무도 안 봐서,
    #   「칼럼만 추가된 변경」은 검사를 그냥 통과하고 첫 요청에서 터질 수 있었다.
    _REQUIRED_COLUMNS = (
        ("captcha_challenges_v2", "lecture_id"),
        ("captcha_tokens", "lecture_id"),
        ("captcha_questions", "served_count"),
        ("captcha_questions", "last_served_at"),
        ("captcha_challenges_v2", "pow_bits"),
        ("captcha_challenges_v2", "honeypot_ids"),
    )

    # ★CREATE TABLE 본문에서 칼럼 이름을 뽑는다.
    #   ⚠️줄 단위로 뽑으면 안 된다 — 한 줄에 칼럼이 여러 개 있어서 첫 개만 잡힌다.
    #     (0813 에 실제로 그렇게 세다가 118개를 57개로 잘못 셌다)
    #   그래서 ★괄호 깊이를 세며 최상위 쉼표로 자른다. DECIMAL(10,2) 같은 것이 안 잘리게.
    _COLUMN_TYPES = (
        r"(VARCHAR|INT|BIGINT|TINYINT|DATETIME|JSON|LONGTEXT|MEDIUMTEXT|TEXT|CHAR|DECIMAL"
        r"|FLOAT|DOUBLE|BOOLEAN|BOOL|ENUM|BLOB|SMALLINT|MEDIUMINT|TIMESTAMP|DATE)\b")
    _NOT_A_COLUMN = re.compile(
        r"^\s*(PRIMARY|UNIQUE|INDEX|KEY|CONSTRAINT|FOREIGN|FULLTEXT|SPATIAL|CHECK)\b", re.I)

    @staticmethod
    def _split_top_level(body: str) -> list[str]:
        """괄호 밖의 쉼표로만 자른다."""
        parts, depth, cur = [], 0, ""
        for ch in body:
            if ch == "(":
                depth += 1
            elif ch == ")":
                if depth == 0:      # CREATE TABLE 을 닫는 괄호 — 여기서 끝
                    break
                depth -= 1
            if ch == "," and depth == 0:
                parts.append(cur)
                cur = ""
            else:
                cur += ch
        if cur.strip():
            parts.append(cur)
        return parts

    @classmethod
    def _schema_columns(cls) -> dict[str, set[str]]:
        """SCHEMA + ALTER 전용 목록 → {표: {칼럼}}."""
        want: dict[str, set[str]] = {}
        for statement in SCHEMA:
            table = re.search(r"CREATE TABLE IF NOT EXISTS (\w+)", statement).group(1)
            columns = set()
            for part in cls._split_top_level(statement[statement.index("(") + 1:]):
                text = part.strip()
                if not text or cls._NOT_A_COLUMN.match(text):
                    continue
                found = re.match(r"^`?(\w+)`?\s+" + cls._COLUMN_TYPES, text, re.I)
                if found:
                    columns.add(found.group(1))
            want[table] = columns
        for table, column in cls._REQUIRED_COLUMNS:   # ★ALTER 로만 붙는 것
            want.setdefault(table, set()).add(column)
        return want

    def _verify_schema(self) -> None:
        """DDL 을 하지 않는 모드에서, 있어야 할 표·칼럼이 다 있는지 본다.

        ★없으면 기동을 막는다. 조용히 뜬 뒤 첫 요청에서 "그런 표 없음" 이 나면
        원인이 멀어진다 — 비밀값 로더와 같은 원칙이다.
        """
        want_columns = self._schema_columns()
        want = list(want_columns)
        with self.connection(True) as conn, conn.cursor() as cur:
            cur.execute("SHOW TABLES")
            have = {list(row.values())[0] for row in cur.fetchall()}
            missing_tables = [t for t in want if t not in have]
            missing_cols = []
            # ★표마다 한 번씩만 묻는다 — 칼럼마다 물으면 질의가 100번을 넘는다.
            for table in want:
                if table in missing_tables:
                    continue
                cur.execute(
                    "SELECT COLUMN_NAME c FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s", (table,))
                present = {row["c"] for row in cur.fetchall()}
                for column in sorted(want_columns[table]):
                    if column not in present:
                        missing_cols.append(f"{table}.{column}")
        if missing_tables or missing_cols:
            raise RuntimeError(
                "SCHEMA_MANAGED_EXTERNALLY=true 인데 스키마가 모자랍니다 — "
                f"없는 표 {missing_tables or '없음'} · 없는 칼럼 {missing_cols or '없음'}. "
                "catchap_dba 로 스키마를 먼저 적용하십시오(deploy/schema.sql).")

    def initialize(self) -> None:
        if self.settings.schema_managed_externally:
            self._verify_schema()
            return
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT GET_LOCK('security_captcha_v2_schema', 20) acquired")
            if cur.fetchone()["acquired"] != 1: raise RuntimeError("schema lock unavailable")
            try:
                for statement in SCHEMA: cur.execute(statement)
                conn.commit()
                # 기존 DB용 멱등 마이그레이션(컬럼 이미 있으면 무시)
                for alter in ("ALTER TABLE captcha_challenges_v2 ADD COLUMN lecture_id VARCHAR(128) NULL",
                              "ALTER TABLE captcha_tokens ADD COLUMN lecture_id VARCHAR(128) NULL",
                              "ALTER TABLE captcha_questions ADD COLUMN served_count INT NOT NULL DEFAULT 0",
                              "ALTER TABLE captcha_questions ADD COLUMN last_served_at DATETIME(6) NULL",
                              "ALTER TABLE captcha_challenges_v2 ADD COLUMN pow_bits TINYINT UNSIGNED NOT NULL DEFAULT 0",
                              "ALTER TABLE captcha_challenges_v2 ADD COLUMN honeypot_ids TEXT NULL"):
                    try: cur.execute(alter); conn.commit()
                    except Exception: conn.rollback()
            finally: cur.execute("SELECT RELEASE_LOCK('security_captcha_v2_schema')")

    def ping(self) -> bool:
        with self.connection(True) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 ok"); return cur.fetchone()["ok"] == 1

    def behavior_shadow_summary(self, days: int) -> dict[str, Any]:
        """behavior-AI 승격 준비도. 사람 프록시(정답 통과)에 대한 오탐 프록시(step_up율)를 계산."""
        empty = {"table": False, "total": 0}
        with self.connection(True) as conn, conn.cursor() as cur:
            cur.execute("""SELECT COUNT(*) n FROM information_schema.tables
                           WHERE table_schema=%s AND table_name='behavior_shadow_predictions'""", (self.settings.db_name,))
            if cur.fetchone()["n"] == 0:
                return empty
            cur.execute("""SELECT
                  COUNT(*) total,
                  SUM(recommended_action IN ('step_up','step_up_and_rate_limit')) would_block,
                  SUM(main_captcha_verdict='passed') passed,
                  SUM(main_captcha_verdict='passed' AND recommended_action IN ('step_up','step_up_and_rate_limit')) passed_would_block,
                  SUM(status='scored') scored
                FROM behavior_shadow_predictions
                WHERE created_at > UTC_TIMESTAMP() - INTERVAL %s DAY""", (days,))
            r = cur.fetchone() or {}
            cur.execute("""SELECT COALESCE(recommended_action,'(none)') a, COUNT(*) n
                FROM behavior_shadow_predictions WHERE created_at > UTC_TIMESTAMP() - INTERVAL %s DAY GROUP BY a""", (days,))
            actions = {row["a"]: row["n"] for row in cur.fetchall()}
        passed = int(r.get("passed") or 0); pwb = int(r.get("passed_would_block") or 0)
        return {"table": True, "days": days, "total": int(r.get("total") or 0), "scored": int(r.get("scored") or 0),
                "would_block": int(r.get("would_block") or 0), "passed": passed, "passed_would_block": pwb,
                "fp_proxy_rate": round(pwb / passed, 4) if passed else None, "action_dist": actions}

    def has_active_question(self) -> bool:
        with self.connection(True) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM captcha_questions WHERE status='active' AND review_status='approved' LIMIT 1")
            return cur.fetchone() is not None

    def active_question(self, min_obj: int | None = None, max_obj: int | None = None) -> dict[str, Any] | None:
        """회전 출제: **뽑힐 확률이 1/(노출+1) 에 비례하는 가중 무작위** + 쿨다운.

        ★왜 노출순 정렬이 아닌가 — `ORDER BY served_count ASC` 는 공평하지만, 문항을
          받아가려는 쪽에는 **중복 없는 목록을 순서대로 내주는 것**과 같다. 한 번 받은
          문항은 노출이 올라가 뒤로 밀리므로 다음에는 반드시 안 본 문항이 나온다.
          2026-08-14 실측 — 활성 문항 2,003개의 노출이 전부 3~4회로 붙어 있었고,
          IP 하나가 분당 30개를 받으면 **67분**이면 은행을 한 바퀴 다 본다.

        ★`served_count + RAND()*폭` 은 **효과가 없다.** 2,003행 중 **최솟값 하나**를
          뽑는데, 노출 적은 무리가 크면 그 안에서 아주 작은 난수가 반드시 나온다.
          실측(0814): 3회 무리 533개의 최솟값 ≈ 3.047, 4회 무리 1,470개는 ≈ 4.017 이라
          적은 무리가 **100%** 이긴다. 폭을 25에서 2000으로 키워도 43% 다.
          (한 번 그렇게 넣었다가 되돌렸다 — catchap-captcha#41)

        ★지수 경주로 뽑는다. `-LOG(1-RAND()) * (노출+1)` 의 최솟값을 고르면 뽑힐 확률이
          `1/(노출+1)` 에 비례한다. 적게 나온 쪽이 유리하되 많이 나온 쪽도 나온다.
          모의 실험(2,003문항·40,000회): 노출 편차 2.78(완전 무작위 4.38보다 작다),
          한 바퀴 10,814회(지금 2,003회의 5.4배 = 분당 30개면 67분에서 약 6시간).

        min_obj/max_obj 지정 시 실객체(invalid 제외) 수가 그 범위인 문항만(step-up 계층용)."""
        cooldown = self.settings.rotation_cooldown_seconds
        # 실객체 수 범위 필터(step-up). 없으면 전체.
        oc_join = (" JOIN (SELECT question_id, SUM(role<>'invalid') oc FROM captcha_objects GROUP BY question_id) c"
                   " ON c.question_id=q.id AND c.oc BETWEEN %s AND %s") if min_obj is not None else ""
        oc_args = (min_obj, max_obj) if min_obj is not None else ()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(f"""SELECT q.* FROM captcha_questions q{oc_join}
              WHERE q.status='active' AND q.review_status='approved'
                AND (q.last_served_at IS NULL OR q.last_served_at < UTC_TIMESTAMP(6) - INTERVAL %s SECOND)
              ORDER BY -LOG(1 - RAND()) * (q.served_count + 1) ASC LIMIT 1""",
                        (*oc_args, cooldown))
            question = cur.fetchone()
            if not question:  # 전부 쿨다운 중(풀이 작을 때)이면 쿨다운 무시
                cur.execute(f"""SELECT q.* FROM captcha_questions q{oc_join}
                  WHERE q.status='active' AND q.review_status='approved'
                  ORDER BY -LOG(1 - RAND()) * (q.served_count + 1) ASC LIMIT 1""", oc_args)
                question = cur.fetchone()
            if not question: conn.commit(); return None
            cur.execute("UPDATE captcha_questions SET served_count=served_count+1, last_served_at=UTC_TIMESTAMP(6) WHERE id=%s", (question["id"],))
            cur.execute("SELECT * FROM captcha_objects WHERE question_id=%s ORDER BY id", (question["id"],))
            question["objects"] = cur.fetchall(); conn.commit(); return question

    def top_exposed(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connection(True) as conn, conn.cursor() as cur:
            cur.execute("""SELECT id, instruction_ko, served_count, last_served_at FROM captcha_questions
              WHERE status='active' AND review_status='approved' ORDER BY served_count DESC LIMIT %s""", (limit,))
            return cur.fetchall()

    def rest_question(self, question_id: str) -> bool:
        """과다 노출(탄) 문항을 출제 풀에서 내림(rested). 되돌리려면 status를 active로."""
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("UPDATE captcha_questions SET status='rested' WHERE id=%s AND status='active'", (question_id,))
            ok = cur.rowcount == 1; conn.commit(); return ok

    def create_challenge(self, challenge: dict[str, Any], mappings: list[tuple[int, str]], behavior_nonce: str) -> None:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO captcha_challenges_v2
              (id,question_id,session_id,purpose,lecture_id,expires_at,status,created_at,client_ip_hash,pow_bits,honeypot_ids)
              VALUES(%s,%s,%s,%s,%s,%s,'issued',%s,%s,%s,%s)""",
              tuple(challenge.get(k) for k in ("id","question_id","session_id","purpose","lecture_id","expires_at","created_at","client_ip_hash","pow_bits","honeypot_ids")))
            cur.executemany("INSERT INTO captcha_challenge_objects(challenge_id,object_id,temporary_object_id) VALUES(%s,%s,%s)",
                            [(challenge["id"], object_id, temporary) for object_id, temporary in mappings])
            cur.execute(
                """INSERT INTO captcha_behavior_sessions(challenge_id,nonce_hash,created_at)
                VALUES(%s,%s,%s)""",
                (challenge["id"], hashlib.sha256(behavior_nonce.encode("utf-8")).hexdigest(), challenge["created_at"]),
            )
            conn.commit()
        # ★커밋 뒤에 센다 — 캐시가 실패해도 DB 는 이미 끝나 있다.
        self.cache.bump("ip_challenges_1m", challenge.get("client_ip_hash") or "")
        self.cache.bump("session_challenges_10m", challenge.get("session_id") or "")

    def decoy_piece_path(self, exclude_question_id: str, seed: str) -> str | None:
        """함정에게 입혀 줄 **진짜 조각**의 경로. 같은 씨앗이면 늘 같은 것이 나온다.

        왜 다른 문항의 진짜 조각인가 — 함정 미리보기가 404 라서 봇이 문제를 풀기도 전에
        함정을 전부 걸러냈다(2026-08-13 실측: 10문항 3개씩 전부 적중, 캡차 시도 0회).
        사진에서 그 자리를 네모로 잘라 주는 방법도 있지만, 진짜 조각은 배경이 뚫린
        오려낸 그림이고 네모 크롭은 불투명한 사각형이라 그림만 봐도 갈린다.

        같은 문항의 조각은 뺀다. 사진 안에 있는 물건이 함정 자리에서 또 나오면
        사람 눈에 이상하고, 봇에게는 "이 문항 안의 조각 = 함정" 이라는 새 단서가 된다.
        """
        with self.connection(True) as conn, conn.cursor() as cur:
            if self._piece_total is None:
                cur.execute("""SELECT COUNT(*) n FROM captcha_objects
                  WHERE piece_path IS NOT NULL AND piece_path<>''""")
                self._piece_total = int(cur.fetchone()["n"])
            if not self._piece_total:
                return None
            offset = int(hashlib.sha256(seed.encode()).hexdigest(), 16) % self._piece_total
            for candidate in (offset, 0):
                cur.execute("""SELECT piece_path FROM captcha_objects
                  WHERE piece_path IS NOT NULL AND piece_path<>'' AND question_id<>%s
                  ORDER BY id LIMIT %s,1""", (exclude_question_id, candidate))
                row = cur.fetchone()
                if row:
                    return row["piece_path"]
            return None

    def request_pattern(self, session_id: str, client_ip_hash: str) -> dict[str, int]:
        with self.connection(True) as conn, conn.cursor() as cur:
            cur.execute("""SELECT COUNT(*) n FROM captcha_challenges_v2
              WHERE client_ip_hash=%s AND created_at>UTC_TIMESTAMP(6)-INTERVAL 1 MINUTE""", (client_ip_hash,))
            ip_challenges_1m=int(cur.fetchone()["n"])
            cur.execute("""SELECT COUNT(*) n FROM captcha_challenges_v2
              WHERE session_id=%s AND created_at>UTC_TIMESTAMP(6)-INTERVAL 10 MINUTE""", (session_id,))
            session_challenges_10m=int(cur.fetchone()["n"])
            cur.execute("""SELECT COUNT(*) n FROM captcha_attempts a
              JOIN captcha_challenges_v2 c ON c.id=a.challenge_id
              WHERE c.session_id=%s AND a.is_correct=0
                AND a.created_at>UTC_TIMESTAMP(6)-INTERVAL 10 MINUTE""", (session_id,))
            session_failures_10m=int(cur.fetchone()["n"])
            cur.execute("""SELECT COUNT(*) n FROM behavior_shadow_predictions p
              JOIN captcha_attempts a ON a.id=p.captcha_attempt_id
              JOIN captcha_challenges_v2 c ON c.id=a.challenge_id
              WHERE c.session_id=%s AND p.final_verdict='failed' AND p.status='unavailable'
                AND p.created_at>UTC_TIMESTAMP(6)-INTERVAL 10 MINUTE""", (session_id,))
            session_telemetry_failures_10m=int(cur.fetchone()["n"])
            # 봇 의심(중간·높음)이 최근에 몇 번 붙었나. 틀린 횟수와 **다른 것**을 가리킨다 —
            # 틀림은 문제를 못 푸는 쪽(찍는 봇), 의심은 문제는 푸는데 궤적이 기계인 쪽이다.
            # 그래서 대응도 나뉜다(`create_challenge`).
            cur.execute("""SELECT COUNT(*) n FROM behavior_shadow_predictions p
              JOIN captcha_attempts a ON a.id=p.captcha_attempt_id
              JOIN captcha_challenges_v2 c ON c.id=a.challenge_id
              WHERE c.session_id=%s AND p.risk_level IN ('medium','high')
                AND p.created_at>UTC_TIMESTAMP(6)-INTERVAL 10 MINUTE""", (session_id,))
            session_suspicious_10m=int(cur.fetchone()["n"])
            # 마지막 오답 시각 — 대기 시간을 재는 기준점.
            cur.execute("""SELECT MAX(a.created_at) t FROM captcha_attempts a
              JOIN captcha_challenges_v2 c ON c.id=a.challenge_id
              WHERE c.session_id=%s AND a.is_correct=0
                AND a.created_at>UTC_TIMESTAMP(6)-INTERVAL 10 MINUTE""", (session_id,))
            last_failure_at=cur.fetchone()["t"]
        db_values = {"ip_challenges_1m":ip_challenges_1m,"session_challenges_10m":session_challenges_10m,
                     "session_failures_10m":session_failures_10m,
                     "session_telemetry_failures_10m":session_telemetry_failures_10m,
                     "session_suspicious_10m":session_suspicious_10m,
                     "last_failure_at":last_failure_at}
        # ★1단계 — 캐시에도 같은 것을 세어 두고 ★값이 다를 때만 로그로 남긴다.
        #   판정은 여전히 DB 값으로 한다. 캐시가 죽어도 아무 일도 안 일어난다.
        #
        # ⚠️캐시가 세는 것은 ★아래 넷뿐이다.
        #     session_suspicious_10m  은 캐시로 안 센다 (봇 의심 판정이 필요하다)
        #     last_failure_at         은 ★날짜라 카운터로 못 센다
        #   그래서 비교도, 갈아끼우기도 ★이 넷만 한다. 통째로 바꾸면 위 둘이
        #   사라져 main.py 가 KeyError 로 터진다.
        if self.cache.enabled:
            cache_values = self.cache.read({
                "ip_challenges_1m": client_ip_hash,
                "session_challenges_10m": session_id,
                "session_failures_10m": session_id,
                "session_telemetry_failures_10m": session_id,
            })
            counted = {k: db_values[k] for k in CACHED_COUNTERS}
            compare_and_log(counted, cache_values)
            if self.settings.valkey_rate_limit_mode == "cache" and cache_values is not None:
                return {**db_values, **cache_values}      # ★덮어쓰기 — 지우지 않는다
        return db_values

    def challenge_for_verify(self, challenge_id: str) -> dict[str, Any] | None:
        with self.connection(True) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM captcha_challenges_v2 WHERE id=%s", (challenge_id,)); challenge=cur.fetchone()
            if not challenge: return None
            cur.execute("""SELECT m.temporary_object_id,m.object_id,o.role,o.piece_path,
              o.bbox_x,o.bbox_y,o.bbox_width,o.bbox_height FROM captcha_challenge_objects m
              JOIN captcha_objects o ON o.id=m.object_id WHERE m.challenge_id=%s""", (challenge_id,))
            challenge["objects"] = cur.fetchall(); return challenge

    def append_behavior_batch(
        self,
        *,
        challenge_id: str,
        nonce: str,
        batch_seq: int,
        previous_receipt: str | None,
        events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Append one browser batch and return a retry-safe server receipt.

        The browser never chooses a receipt or a receive time. A lost HTTP
        response can safely retry the exact same batch sequence and payload.
        """
        payload_hash = _payload_hash(events)
        nonce_hash = hashlib.sha256(nonce.encode("utf-8")).hexdigest()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT nonce_hash,next_batch_seq,last_receipt_hash,received_event_count FROM captcha_behavior_sessions
                WHERE challenge_id=%s FOR UPDATE""",
                (challenge_id,),
            )
            session = cur.fetchone()
            if not session:
                raise ValueError("behavior_session_missing")
            if not hmac.compare_digest(session["nonce_hash"], nonce_hash):
                raise ValueError("behavior_nonce_invalid")

            cur.execute(
                """SELECT payload_hash,receipt_hash,received_at FROM captcha_behavior_batches
                WHERE challenge_id=%s AND batch_seq=%s""",
                (challenge_id, batch_seq),
            )
            existing = cur.fetchone()
            if existing:
                if not hmac.compare_digest(existing["payload_hash"], payload_hash):
                    raise ValueError("behavior_batch_conflict")
                conn.commit()
                return {
                    "receipt": existing["receipt_hash"],
                    "server_received_at": _receipt_timestamp(existing["received_at"]),
                    "duplicate": True,
                }

            if batch_seq != int(session["next_batch_seq"]):
                raise ValueError("behavior_batch_out_of_order")
            expected_event_seq = int(session["received_event_count"])
            for offset, event in enumerate(events):
                if int(event.get("seq", -1)) != expected_event_seq + offset:
                    raise ValueError("behavior_event_sequence_invalid")
            expected_previous = session["last_receipt_hash"]
            if not hmac.compare_digest(previous_receipt or "", expected_previous or ""):
                raise ValueError("behavior_receipt_chain_invalid")

            received_at = utcnow()
            receipt = _receipt_hash(challenge_id, batch_seq, expected_previous, payload_hash, received_at)
            cur.execute(
                """INSERT INTO captcha_behavior_batches(
                  challenge_id,batch_seq,event_count,previous_receipt_hash,payload_hash,receipt_hash,events_json,received_at
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    challenge_id,
                    batch_seq,
                    len(events),
                    expected_previous,
                    payload_hash,
                    receipt,
                    json.dumps(_canonical_events(events), ensure_ascii=False, separators=(",", ":")),
                    received_at,
                ),
            )
            cur.execute(
                """UPDATE captcha_behavior_sessions
                SET next_batch_seq=next_batch_seq+1,last_receipt_hash=%s,
                    received_event_count=received_event_count+%s
                WHERE challenge_id=%s""",
                (receipt, len(events), challenge_id),
            )
            conn.commit()
        return {"receipt": receipt, "server_received_at": _receipt_timestamp(received_at), "duplicate": False}

    def trusted_behavior_events(self, challenge_id: str) -> tuple[list[dict[str, Any]], str | None]:
        """Return only contiguous, receipt-validated events for final scoring."""
        with self.connection(True) as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT next_batch_seq,last_receipt_hash,received_event_count FROM captcha_behavior_sessions
                WHERE challenge_id=%s""",
                (challenge_id,),
            )
            session = cur.fetchone()
            if not session:
                return [], "behavior_session_missing"
            cur.execute(
                """SELECT batch_seq,event_count,previous_receipt_hash,payload_hash,receipt_hash,events_json,received_at
                FROM captcha_behavior_batches WHERE challenge_id=%s ORDER BY batch_seq""",
                (challenge_id,),
            )
            rows = cur.fetchall()

        return _validate_behavior_batches(challenge_id, session, rows)

    def behavior_batch_received_at(self, challenge_id: str) -> list[datetime]:
        """Return server-assigned receipt times for a previously validated batch stream."""
        with self.connection(True) as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT received_at FROM captcha_behavior_batches
                WHERE challenge_id=%s ORDER BY batch_seq""",
                (challenge_id,),
            )
            return [row["received_at"] for row in cur.fetchall()]

    def get_question(self, question_id: str) -> dict[str, Any] | None:
        with self.connection(True) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM captcha_questions WHERE id=%s", (question_id,)); question=cur.fetchone()
            if not question: return None
            cur.execute("SELECT * FROM captcha_objects WHERE question_id=%s ORDER BY id", (question_id,))
            question["objects"]=cur.fetchall(); return question

    def record_attempt(self, challenge_id: str, selected: list[str], correct: bool, reason: str | None,
                       duration_ms: int, summary: dict[str, Any], raw_path: str | None) -> int:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO captcha_attempts(challenge_id,selected_object_ids,is_correct,failure_reason,
              duration_ms,behavior_summary,raw_event_path,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
              (challenge_id,json.dumps(selected),correct,reason,duration_ms,json.dumps(summary),raw_path,utcnow()))
            attempt_id=cur.lastrowid
            cur.execute("""INSERT INTO behavior_summaries(attempt_id,reaction_time_ms,total_duration_ms,drag_count,
              wrong_object_count,average_speed,speed_variance,path_length,path_curvature,pause_count)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (attempt_id,summary.get("reaction_time_ms"),duration_ms,
              summary["drag_count"],summary["wrong_object_count"],summary["average_speed"],summary["speed_variance"],
              summary["path_length"],summary["path_curvature"],summary["pause_count"]))
            cur.execute("UPDATE captcha_challenges_v2 SET attempt_count=attempt_count+1,status=%s,verified_at=%s WHERE id=%s",
                        ("passed" if correct else "failed", utcnow() if correct else None, challenge_id))
            # ★세션 실패 카운터는 session_id 로 세는데 여기에는 challenge_id 밖에 없다.
            #   이미 열려 있는 커서로 한 번만 더 읽는다 — 연결을 새로 열지 않는다.
            session_id = ""
            if not correct and self.cache.enabled:
                cur.execute("SELECT session_id FROM captcha_challenges_v2 WHERE id=%s", (challenge_id,))
                row = cur.fetchone()
                session_id = (row or {}).get("session_id") or ""
            conn.commit()
        if session_id:
            self.cache.bump("session_failures_10m", session_id)
        return int(attempt_id)

    def record_behavior_shadow_prediction(
        self,
        captcha_attempt_id: int,
        prediction: Any,
        local_policy_mode: str,
        main_captcha_verdict: str,
        final_verdict: str,
    ) -> None:
        """Persist AI output beside the local attempt without storing answers."""
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO behavior_shadow_predictions(
                  captcha_attempt_id,behavior_attempt_id,status,detail,local_policy_mode,model_policy_mode,
                  risk_score,risk_level,recommended_action,human_score,bot_risk_score,model_name,model_version,
                  feature_schema_version,reasons,main_captcha_verdict,final_verdict,created_at)
                  VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                  ON DUPLICATE KEY UPDATE status=VALUES(status),detail=VALUES(detail),
                  model_policy_mode=VALUES(model_policy_mode),risk_score=VALUES(risk_score),
                  risk_level=VALUES(risk_level),recommended_action=VALUES(recommended_action),
                  human_score=VALUES(human_score),bot_risk_score=VALUES(bot_risk_score),
                  model_name=VALUES(model_name),model_version=VALUES(model_version),
                  feature_schema_version=VALUES(feature_schema_version),reasons=VALUES(reasons),
                  main_captcha_verdict=VALUES(main_captcha_verdict),final_verdict=VALUES(final_verdict)""",
                (
                    captcha_attempt_id, prediction.attempt_id, prediction.status, prediction.detail,
                    local_policy_mode, prediction.policy_mode, prediction.risk_score, prediction.risk_level,
                    prediction.recommended_action, prediction.human_score, prediction.bot_risk_score,
                    prediction.model_name, prediction.model_version, prediction.feature_schema_version,
                    json.dumps(list(prediction.reasons)), main_captcha_verdict, final_verdict, utcnow(),
                ),
            )
            conn.commit()

    def purge_expired_tokens(self, *, limit: int | None = None, max_batches: int = 50) -> int:
        """만료된 토큰을 지운다. 지운 행수를 돌려준다.

        ★왜 이것만 지우나 — `captcha_tokens` 를 참조하는 표가 ★하나도 없다.
          그래서 이 삭제는 아무것도 딸려 가지 않는다. 반대로 부모인
          `captcha_challenges_v2` 를 지우면 CASCADE 로 행동 데이터까지 사라진다.
          (0813 성원님 판단 — 부모는 그대로 두고 필요하면 자식을 직접 지운다)

        ★한 번에 다 지우지 않고 나눠 지운다. 큰 DELETE 는 잠금을 오래 잡아
        그동안 토큰 발급이 밀린다.

        ⚠️`expires_at` 에 색인이 없다. 지금 크기(3천 행)에서는 훑어도 금방이고,
          이 정리가 돌면 표가 계속 작게 유지된다. 색인은 DDL 이라 앱 계정으로
          만들 수 없다(0810 회수) — 표가 커지면 catchap_dba 로 넣는다.
        """
        batch = limit or self.settings.token_purge_batch
        지운수 = 0
        for _ in range(max_batches):
            with self.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM captcha_tokens "
                    "WHERE expires_at < UTC_TIMESTAMP(6) - INTERVAL %s HOUR LIMIT %s",
                    (self.settings.token_retention_hours, batch))
                n = cur.rowcount
                conn.commit()
            지운수 += n
            if n < batch:          # 더 지울 것이 없다
                break
        return 지운수

    def create_token(self, challenge_id: str, token_hash: str, purpose: str, session_id: str,
                     expires_at: datetime, lecture_id: str | None = None) -> None:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO captcha_tokens(challenge_id,token_hash,purpose,lecture_id,session_id,expires_at,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                        (challenge_id,token_hash,purpose,lecture_id,session_id,expires_at,utcnow())); conn.commit()

    def consume_token(self, token_hash: str, purpose: str, session_id: str) -> bool:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("""UPDATE captcha_tokens SET consumed_at=%s WHERE token_hash=%s AND purpose=%s AND session_id=%s
              AND consumed_at IS NULL AND expires_at>%s""", (utcnow(),token_hash,purpose,session_id,utcnow()))
            ok=cur.rowcount==1; conn.commit(); return ok

    def verify_token(self, token_hash: str, purpose: str, session_id: str,
                     lecture_id: str | None = None) -> dict[str, Any] | None:
        """서버-투-서버 토큰 검증 후 1회 소비. 유효하면 토큰 정보, 아니면 None."""
        now = utcnow()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("""SELECT challenge_id, lecture_id FROM captcha_tokens
              WHERE token_hash=%s AND purpose=%s AND session_id=%s AND consumed_at IS NULL AND expires_at>%s
              FOR UPDATE""", (token_hash, purpose, session_id, now))
            row = cur.fetchone()
            if not row:
                conn.commit(); return None
            if lecture_id is not None and (row.get("lecture_id") or "") != lecture_id:
                conn.commit(); return None
            cur.execute("UPDATE captcha_tokens SET consumed_at=%s WHERE token_hash=%s AND consumed_at IS NULL", (now, token_hash))
            consumed = cur.rowcount == 1; conn.commit()
            return {"challenge_id": row["challenge_id"], "lecture_id": row.get("lecture_id")} if consumed else None

    # ---- 행동 지문(클러스터) : 공유 풀이툴 탐지 ----
    def record_fingerprint(self, session_id: str, signature: str, risk_score: int) -> None:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO captcha_behavior_fingerprints(session_id,signature,risk_score,created_at) VALUES(%s,%s,%s,%s)",
                        (session_id, signature, int(risk_score), utcnow())); conn.commit()

    def signature_cluster_size(self, signature: str, hours: int = 24) -> int:
        """이 시그니처를 최근 window 안에 만든 '서로 다른 세션' 수."""
        with self.connection(True) as conn, conn.cursor() as cur:
            cur.execute("""SELECT COUNT(DISTINCT session_id) n FROM captcha_behavior_fingerprints
              WHERE signature=%s AND created_at>UTC_TIMESTAMP(6)-INTERVAL %s HOUR""", (signature, hours))
            return int(cur.fetchone()["n"])

    def top_signature_clusters(self, hours: int = 24, min_sessions: int = 5, limit: int = 50) -> list[dict[str, Any]]:
        with self.connection(True) as conn, conn.cursor() as cur:
            cur.execute("""SELECT signature, COUNT(DISTINCT session_id) sessions, COUNT(*) attempts, ROUND(AVG(risk_score),1) avg_risk
              FROM captcha_behavior_fingerprints WHERE created_at>UTC_TIMESTAMP(6)-INTERVAL %s HOUR
              GROUP BY signature HAVING sessions>=%s ORDER BY sessions DESC LIMIT %s""", (hours, min_sessions, limit))
            return cur.fetchall()

    def create_user(self, user_id: str, email: str, password_hash: str) -> None:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO captcha_users(id,email,password_hash,created_at) VALUES(%s,%s,%s,%s)",
                        (user_id,email,password_hash,utcnow())); conn.commit()

    def claim_review_batch(self, queue_ids: list[str], reviewer_id: str, batch_size: int = 50,
                           lease_minutes: int = 120) -> set[str]:
        now=utcnow(); expires=now+timedelta(minutes=lease_minutes)
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM captcha_review_claims WHERE expires_at<=%s", (now,))
            cur.execute("""SELECT queue_id FROM captcha_review_claims
              WHERE reviewer_id=%s AND expires_at>%s ORDER BY claimed_at LIMIT %s""",
              (reviewer_id,now,batch_size))
            claimed={str(row["queue_id"]) for row in cur.fetchall()}
            for queue_id in queue_ids:
                if len(claimed)>=batch_size: break
                cur.execute("""INSERT IGNORE INTO captcha_review_claims
                  (queue_id,reviewer_id,claimed_at,expires_at) VALUES(%s,%s,%s,%s)""",
                  (queue_id,reviewer_id,now,expires))
                if cur.rowcount: claimed.add(queue_id)
            conn.commit()
        return claimed

    def claim_decision(self, queue_id: str, reviewer_id: str, lease_minutes: int = 120) -> bool:
        """검수 확정 순간의 원자적 선점 잠금. 먼저 잡은 사람만 True.
        이미 같은 사람이 잡고 있으면 True(재저장 허용), 다른 사람이면 False."""
        now = utcnow(); expires = now + timedelta(minutes=lease_minutes)
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM captcha_review_claims WHERE expires_at<=%s", (now,))
            cur.execute("""INSERT IGNORE INTO captcha_review_claims
              (queue_id,reviewer_id,claimed_at,expires_at) VALUES(%s,%s,%s,%s)""",
              (queue_id, reviewer_id, now, expires))
            if cur.rowcount == 1:
                conn.commit(); return True
            cur.execute("SELECT reviewer_id FROM captcha_review_claims WHERE queue_id=%s", (queue_id,))
            row = cur.fetchone(); conn.commit()
            return bool(row) and str(row["reviewer_id"]) == reviewer_id

    def release_review_claim(self, queue_id: str, reviewer_id: str | None = None) -> None:
        with self.connection() as conn, conn.cursor() as cur:
            if reviewer_id:
                cur.execute("DELETE FROM captcha_review_claims WHERE queue_id=%s AND reviewer_id=%s",
                            (queue_id,reviewer_id))
            else:
                cur.execute("DELETE FROM captcha_review_claims WHERE queue_id=%s", (queue_id,))
            conn.commit()

    # ---- 검수 결정: DB를 단일 소스로 사용 ----
    def decision_map(self) -> dict[str, dict[str, Any]]:
        """{queue_id: {'review_status':..., 'reviewer':...}} 전체 결정 맵."""
        with self.connection(True) as conn, conn.cursor() as cur:
            cur.execute("SELECT queue_id,review_status,reviewer FROM captcha_review_decisions")
            return {str(r["queue_id"]): r for r in cur.fetchall()}

    def get_decision(self, queue_id: str) -> dict[str, Any] | None:
        with self.connection(True) as conn, conn.cursor() as cur:
            cur.execute("SELECT queue_id,review_status,reviewer FROM captcha_review_decisions WHERE queue_id=%s", (queue_id,))
            return cur.fetchone()

    def active_question_ids(self) -> set[str]:
        """이미 캡챠에 활성 등록된 문항 id 집합(중복 승인 방지용)."""
        with self.connection(True) as conn, conn.cursor() as cur:
            cur.execute("SELECT id FROM captcha_questions WHERE status='active' AND review_status='approved'")
            return {str(r["id"]) for r in cur.fetchall()}

    def review_counts(self) -> dict[str, int]:
        with self.connection(True) as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) n FROM captcha_questions WHERE status='active' AND review_status='approved'")
            approved = int(cur.fetchone()["n"])
            cur.execute("SELECT COUNT(*) n FROM captcha_review_decisions WHERE review_status='rejected'")
            rejected = int(cur.fetchone()["n"])
            return {"approved": approved, "rejected": rejected}

    def record_decision(self, queue_id: str, review_status: str, reviewer: str, question_id: str | None = None) -> bool:
        """원자적 선점 저장. 이미 다른 검수자가 승인/제외했으면 False."""
        now = utcnow()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT review_status,reviewer FROM captcha_review_decisions WHERE queue_id=%s FOR UPDATE", (queue_id,))
            row = cur.fetchone()
            if row and row["review_status"] in ("approved", "rejected") and row["reviewer"] != reviewer:
                conn.commit(); return False
            cur.execute("""INSERT INTO captcha_review_decisions(queue_id,review_status,reviewer,question_id,reviewed_at)
              VALUES(%s,%s,%s,%s,%s)
              ON DUPLICATE KEY UPDATE review_status=VALUES(review_status),reviewer=VALUES(reviewer),
                question_id=VALUES(question_id),reviewed_at=VALUES(reviewed_at)""",
              (queue_id, review_status, reviewer, question_id, now))
            conn.commit(); return True

    def others_claimed_ids(self, reviewer_id: str) -> set[str]:
        """지금 다른 검수자가 보고 있는(claim) 항목 집합."""
        now = utcnow()
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM captcha_review_claims WHERE expires_at<=%s", (now,))
            conn.commit()
            cur.execute("SELECT queue_id FROM captcha_review_claims WHERE reviewer_id<>%s AND expires_at>%s", (reviewer_id, now))
            return {str(r["queue_id"]) for r in cur.fetchall()}

    def touch_claim(self, queue_id: str, reviewer_id: str, ttl_minutes: int = 3) -> bool:
        """현재 보고 있는 항목을 잠깐 선점(하트비트). 내가 잡으면 True, 남이 잡고 있으면 False."""
        now = utcnow(); expires = now + timedelta(minutes=ttl_minutes)
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM captcha_review_claims WHERE expires_at<=%s", (now,))
            cur.execute("INSERT IGNORE INTO captcha_review_claims(queue_id,reviewer_id,claimed_at,expires_at) VALUES(%s,%s,%s,%s)",
                        (queue_id, reviewer_id, now, expires))
            if cur.rowcount == 1:
                conn.commit(); return True
            cur.execute("UPDATE captcha_review_claims SET expires_at=%s WHERE queue_id=%s AND reviewer_id=%s",
                        (expires, queue_id, reviewer_id))
            mine = cur.rowcount == 1
            conn.commit(); return mine

    def upsert_question(self, question: dict[str, Any], objects: list[dict[str, Any]]) -> None:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO captcha_questions(id,type,instruction_ko,instruction_en,source,source_question_id,
              image_path,image_width,image_height,difficulty,status,review_status,reviewer,reviewed_at,created_at)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
              ON DUPLICATE KEY UPDATE instruction_ko=VALUES(instruction_ko),image_path=VALUES(image_path),
              image_width=VALUES(image_width),image_height=VALUES(image_height),difficulty=VALUES(difficulty),
              status=VALUES(status),review_status=VALUES(review_status),reviewer=VALUES(reviewer),reviewed_at=VALUES(reviewed_at)""",
              tuple(question.get(k) for k in ("id","type","instruction_ko","instruction_en","source","source_question_id",
              "image_path","image_width","image_height","difficulty","status","review_status","reviewer","reviewed_at","created_at")))
            cur.executemany("""INSERT INTO captcha_objects(question_id,object_key,label,bbox_x,bbox_y,bbox_width,bbox_height,role,piece_path)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
              ON DUPLICATE KEY UPDATE label=VALUES(label),bbox_x=VALUES(bbox_x),bbox_y=VALUES(bbox_y),
              bbox_width=VALUES(bbox_width),bbox_height=VALUES(bbox_height),role=VALUES(role),piece_path=VALUES(piece_path)""",
              [(question["id"],o["object_key"],o["label"],o["x"],o["y"],o["width"],o["height"],o["role"],o.get("piece_path")) for o in objects])
            conn.commit()
