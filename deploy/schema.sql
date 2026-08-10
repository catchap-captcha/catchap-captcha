-- 캡차 스키마 — ★app/db.py 의 SCHEMA 상수에서 생성한 파일입니다 (2026-08-10)
--
-- ★왜 이 파일이 있나
--   이 앱은 기동할 때마다 CREATE TABLE IF NOT EXISTS 를 했습니다. 그래서 앱 계정
--   `catchap_captcha_app` 에 ★CREATE·ALTER·INDEX·REFERENCES 권한이 있어야 했고,
--   그것은 ★캡차 파드가 뚫리면 표를 지울 수 있다는 뜻입니다.
--
--   SCHEMA_MANAGED_EXTERNALLY=true 로 두면 앱은 ★DDL 을 안 하고 ★확인만 합니다.
--   대신 스키마를 바꿀 때 ★사람이 이 파일을 `catchap_dba` 로 적용합니다.
--
-- ★쓰는 법
--   mysql --defaults-file=<dba.cnf> catchap_captcha < deploy/schema.sql
--   ⚠️`catchap_captcha_app` 으로는 안 됩니다(권한을 회수했습니다) — `catchap_dba` 로.
--
-- ⚠️★app/db.py 의 SCHEMA 를 고치면 ★이 파일도 같이 고쳐야 합니다.
--   앱이 기동할 때 표·칼럼이 있는지 확인하므로, 안 맞으면 ★파드가 안 뜹니다.
--
-- ⚠️아래 ALTER 6개는 ★이미 적용된 DB 에서는 오류가 납니다(칼럼 중복).
--   옛 DB 를 올릴 때만 쓰는 것이고, 지금 운영 DB 에는 이미 다 들어가 있습니다.

CREATE TABLE IF NOT EXISTS captcha_questions (
      id VARCHAR(64) PRIMARY KEY, type VARCHAR(32) NOT NULL,
      instruction_ko VARCHAR(500) NOT NULL, instruction_en VARCHAR(500) NULL,
      source VARCHAR(64) NOT NULL, source_question_id VARCHAR(128) NULL,
      image_path VARCHAR(500) NOT NULL, image_width INT UNSIGNED NOT NULL,
      image_height INT UNSIGNED NOT NULL, difficulty TINYINT UNSIGNED NOT NULL DEFAULT 2,
      status VARCHAR(24) NOT NULL DEFAULT 'draft', review_status VARCHAR(24) NOT NULL DEFAULT 'pending',
      reviewer VARCHAR(128) NULL, reviewed_at DATETIME(6) NULL, created_at DATETIME(6) NOT NULL,
      INDEX idx_question_status(status, review_status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS captcha_objects (
      id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, question_id VARCHAR(64) NOT NULL,
      object_key VARCHAR(128) NOT NULL, label VARCHAR(128) NOT NULL,
      bbox_x DOUBLE NOT NULL, bbox_y DOUBLE NOT NULL, bbox_width DOUBLE NOT NULL, bbox_height DOUBLE NOT NULL,
      role VARCHAR(16) NOT NULL, piece_path VARCHAR(500) NULL,
      UNIQUE KEY uq_question_object(question_id, object_key), INDEX idx_object_question(question_id),
      CONSTRAINT fk_object_question FOREIGN KEY(question_id) REFERENCES captcha_questions(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS captcha_challenges_v2 (
      id CHAR(36) PRIMARY KEY, question_id VARCHAR(64) NOT NULL, session_id VARCHAR(128) NOT NULL,
      purpose VARCHAR(32) NOT NULL, lecture_id VARCHAR(128) NULL, expires_at DATETIME(6) NOT NULL,
      attempt_count TINYINT UNSIGNED NOT NULL DEFAULT 0, status VARCHAR(16) NOT NULL DEFAULT 'issued',
      created_at DATETIME(6) NOT NULL, verified_at DATETIME(6) NULL,
      client_ip_hash CHAR(64) NOT NULL, INDEX idx_challenge_expiry(expires_at),
      INDEX idx_challenge_rate(client_ip_hash, created_at),
      CONSTRAINT fk_challenge_question FOREIGN KEY(question_id) REFERENCES captcha_questions(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS captcha_challenge_objects (
      challenge_id CHAR(36) NOT NULL, object_id BIGINT UNSIGNED NOT NULL,
      temporary_object_id VARCHAR(64) NOT NULL,
      PRIMARY KEY(challenge_id, temporary_object_id), UNIQUE KEY uq_challenge_object(challenge_id, object_id),
      CONSTRAINT fk_map_challenge FOREIGN KEY(challenge_id) REFERENCES captcha_challenges_v2(id) ON DELETE CASCADE,
      CONSTRAINT fk_map_object FOREIGN KEY(object_id) REFERENCES captcha_objects(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS captcha_behavior_sessions (
      challenge_id CHAR(36) PRIMARY KEY, nonce_hash CHAR(64) NOT NULL,
      next_batch_seq INT UNSIGNED NOT NULL DEFAULT 0, last_receipt_hash CHAR(64) NULL,
      received_event_count INT UNSIGNED NOT NULL DEFAULT 0, created_at DATETIME(6) NOT NULL,
      CONSTRAINT fk_behavior_session_challenge FOREIGN KEY(challenge_id)
        REFERENCES captcha_challenges_v2(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS captcha_behavior_batches (
      id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, challenge_id CHAR(36) NOT NULL,
      batch_seq INT UNSIGNED NOT NULL, event_count SMALLINT UNSIGNED NOT NULL,
      previous_receipt_hash CHAR(64) NULL, payload_hash CHAR(64) NOT NULL,
      receipt_hash CHAR(64) NOT NULL, events_json JSON NOT NULL,
      received_at DATETIME(6) NOT NULL,
      UNIQUE KEY uq_behavior_batch(challenge_id, batch_seq),
      INDEX idx_behavior_batch_challenge(challenge_id, batch_seq),
      CONSTRAINT fk_behavior_batch_challenge FOREIGN KEY(challenge_id)
        REFERENCES captcha_challenges_v2(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS captcha_attempts (
      id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, challenge_id CHAR(36) NOT NULL,
      selected_object_ids JSON NOT NULL, is_correct BOOLEAN NOT NULL,
      failure_reason VARCHAR(64) NULL, duration_ms INT UNSIGNED NOT NULL,
      behavior_summary JSON NULL, raw_event_path VARCHAR(500) NULL, created_at DATETIME(6) NOT NULL,
      INDEX idx_attempt_challenge(challenge_id),
      CONSTRAINT fk_attempt_challenge FOREIGN KEY(challenge_id) REFERENCES captcha_challenges_v2(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS behavior_summaries (
      attempt_id BIGINT UNSIGNED PRIMARY KEY, reaction_time_ms INT UNSIGNED NULL,
      total_duration_ms INT UNSIGNED NOT NULL, drag_count INT UNSIGNED NOT NULL,
      wrong_object_count INT UNSIGNED NOT NULL, average_speed DOUBLE NOT NULL,
      speed_variance DOUBLE NOT NULL, path_length DOUBLE NOT NULL,
      path_curvature DOUBLE NOT NULL, pause_count INT UNSIGNED NOT NULL,
      CONSTRAINT fk_summary_attempt FOREIGN KEY(attempt_id) REFERENCES captcha_attempts(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS behavior_shadow_predictions (
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
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS captcha_tokens (
      id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, challenge_id CHAR(36) NOT NULL,
      token_hash CHAR(64) NOT NULL UNIQUE, purpose VARCHAR(32) NOT NULL, lecture_id VARCHAR(128) NULL,
      session_id VARCHAR(128) NOT NULL, expires_at DATETIME(6) NOT NULL, consumed_at DATETIME(6) NULL,
      created_at DATETIME(6) NOT NULL,
      CONSTRAINT fk_token_challenge FOREIGN KEY(challenge_id) REFERENCES captcha_challenges_v2(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS captcha_users (
      id CHAR(36) PRIMARY KEY, email VARCHAR(320) NOT NULL UNIQUE,
      password_hash VARCHAR(255) NOT NULL, created_at DATETIME(6) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS captcha_review_claims (
      queue_id VARCHAR(128) PRIMARY KEY, reviewer_id VARCHAR(128) NOT NULL,
      claimed_at DATETIME(6) NOT NULL, expires_at DATETIME(6) NOT NULL,
      INDEX idx_review_claim_reviewer(reviewer_id), INDEX idx_review_claim_expiry(expires_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS captcha_review_decisions (
      queue_id VARCHAR(160) PRIMARY KEY, review_status VARCHAR(24) NOT NULL,
      reviewer VARCHAR(128) NULL, question_id VARCHAR(64) NULL, reviewed_at DATETIME(6) NOT NULL,
      INDEX idx_review_decision_status(review_status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS captcha_behavior_fingerprints (
      id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, session_id VARCHAR(128) NOT NULL,
      signature CHAR(16) NOT NULL, risk_score INT NOT NULL, created_at DATETIME(6) NOT NULL,
      INDEX idx_fp_sig(signature, created_at), INDEX idx_fp_created(created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ── 옛 DB 용 추가 칼럼 (이미 있으면 오류 — 그때는 넘어가면 됩니다)
ALTER TABLE captcha_challenges_v2 ADD COLUMN lecture_id VARCHAR(128) NULL;
ALTER TABLE captcha_tokens ADD COLUMN lecture_id VARCHAR(128) NULL;
ALTER TABLE captcha_questions ADD COLUMN served_count INT NOT NULL DEFAULT 0;
ALTER TABLE captcha_questions ADD COLUMN last_served_at DATETIME(6) NULL;
ALTER TABLE captcha_challenges_v2 ADD COLUMN pow_bits TINYINT UNSIGNED NOT NULL DEFAULT 0;
ALTER TABLE captcha_challenges_v2 ADD COLUMN honeypot_ids TEXT NULL;
