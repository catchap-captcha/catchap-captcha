from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import pymysql
from pymysql.cursors import DictCursor

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
      purpose VARCHAR(32) NOT NULL, expires_at DATETIME(6) NOT NULL,
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
    """CREATE TABLE IF NOT EXISTS captcha_tokens (
      id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, challenge_id CHAR(36) NOT NULL,
      token_hash CHAR(64) NOT NULL UNIQUE, purpose VARCHAR(32) NOT NULL,
      session_id VARCHAR(128) NOT NULL, expires_at DATETIME(6) NOT NULL, consumed_at DATETIME(6) NULL,
      created_at DATETIME(6) NOT NULL,
      CONSTRAINT fk_token_challenge FOREIGN KEY(challenge_id) REFERENCES captcha_challenges_v2(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS captcha_users (
      id CHAR(36) PRIMARY KEY, email VARCHAR(320) NOT NULL UNIQUE,
      password_hash VARCHAR(255) NOT NULL, created_at DATETIME(6) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
]


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Database:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _connect(self, autocommit: bool = False) -> pymysql.Connection:
        kwargs: dict[str, Any] = dict(
            user=self.settings.db_user, password=self.settings.db_password,
            database=self.settings.db_name, charset="utf8mb4", cursorclass=DictCursor,
            autocommit=autocommit, connect_timeout=5,
        )
        socket_path = Path(self.settings.db_unix_socket)
        if socket_path.exists(): kwargs["unix_socket"] = str(socket_path)
        else: kwargs.update(host=self.settings.db_host, port=self.settings.db_port)
        return pymysql.connect(**kwargs)

    @contextmanager
    def connection(self, autocommit: bool = False) -> Iterator[pymysql.Connection]:
        conn = self._connect(autocommit)
        try: yield conn
        finally: conn.close()

    def initialize(self) -> None:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT GET_LOCK('security_captcha_v2_schema', 20) acquired")
            if cur.fetchone()["acquired"] != 1: raise RuntimeError("schema lock unavailable")
            try:
                for statement in SCHEMA: cur.execute(statement)
                conn.commit()
            finally: cur.execute("SELECT RELEASE_LOCK('security_captcha_v2_schema')")

    def ping(self) -> bool:
        with self.connection(True) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 ok"); return cur.fetchone()["ok"] == 1

    def active_question(self) -> dict[str, Any] | None:
        with self.connection(True) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM captcha_questions WHERE status='active' AND review_status='approved' ORDER BY RAND() LIMIT 1")
            question = cur.fetchone()
            if not question: return None
            cur.execute("SELECT * FROM captcha_objects WHERE question_id=%s ORDER BY id", (question["id"],))
            question["objects"] = cur.fetchall(); return question

    def create_challenge(self, challenge: dict[str, Any], mappings: list[tuple[int, str]]) -> None:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO captcha_challenges_v2
              (id,question_id,session_id,purpose,expires_at,status,created_at,client_ip_hash)
              VALUES(%s,%s,%s,%s,%s,'issued',%s,%s)""",
              tuple(challenge[k] for k in ("id","question_id","session_id","purpose","expires_at","created_at","client_ip_hash")))
            cur.executemany("INSERT INTO captcha_challenge_objects(challenge_id,object_id,temporary_object_id) VALUES(%s,%s,%s)",
                            [(challenge["id"], object_id, temporary) for object_id, temporary in mappings])
            conn.commit()

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
        return {"ip_challenges_1m":ip_challenges_1m,"session_challenges_10m":session_challenges_10m,
                "session_failures_10m":session_failures_10m}

    def challenge_for_verify(self, challenge_id: str) -> dict[str, Any] | None:
        with self.connection(True) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM captcha_challenges_v2 WHERE id=%s", (challenge_id,)); challenge=cur.fetchone()
            if not challenge: return None
            cur.execute("""SELECT m.temporary_object_id,m.object_id,o.role,o.piece_path FROM captcha_challenge_objects m
              JOIN captcha_objects o ON o.id=m.object_id WHERE m.challenge_id=%s""", (challenge_id,))
            challenge["objects"] = cur.fetchall(); return challenge

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
            conn.commit(); return int(attempt_id)

    def create_token(self, challenge_id: str, token_hash: str, purpose: str, session_id: str, expires_at: datetime) -> None:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO captcha_tokens(challenge_id,token_hash,purpose,session_id,expires_at,created_at) VALUES(%s,%s,%s,%s,%s,%s)",
                        (challenge_id,token_hash,purpose,session_id,expires_at,utcnow())); conn.commit()

    def consume_token(self, token_hash: str, purpose: str, session_id: str) -> bool:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("""UPDATE captcha_tokens SET consumed_at=%s WHERE token_hash=%s AND purpose=%s AND session_id=%s
              AND consumed_at IS NULL AND expires_at>%s""", (utcnow(),token_hash,purpose,session_id,utcnow()))
            ok=cur.rowcount==1; conn.commit(); return ok

    def create_user(self, user_id: str, email: str, password_hash: str) -> None:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO captcha_users(id,email,password_hash,created_at) VALUES(%s,%s,%s,%s)",
                        (user_id,email,password_hash,utcnow())); conn.commit()

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
