# sw-captcha ← ms 병합 가이드

정본은 **sw-captcha**(sw 유지). 이 저장소(`catchap-captcha/ai-service`, `origin/ms`)의 방어 기능을 sw-captcha로 가져가기 위한 안내. 최신 반영: `c0502f4`.

## 현황
| | sw-captcha (정본) | ms (이 저장소) |
|---|---|---|
| 배치채널(영수증체인)·behavior-AI 연동·rich health | ✅ 있음 | 브릿지만(shadow), 배치채널 ❌ |
| **PoW / 적응형 PoW** | ❌ 없음 | ✅ 있음 |
| **허니팟 · 드롭존 랜덤화** | ? diff 확인 | ✅ 있음 |
| 클러스터·레이트리밋·자동화탐지·Origin·문항회전 | ? diff 확인 | ✅ 있음 |
| **승격 준비도 엔드포인트/런북** | ? | ✅ 있음 |

목표: sw-captcha에 **없는 것만** 골라 이식. 최소 **PoW + 허니팟/드롭존**.

---

## 1. 필수 — PoW (커밋 `e59c86c` + `20aefe1`)
- **config**: `POW_ENABLED`, `POW_DIFFICULTY_BITS`(17), `POW_STEPUP_BITS`(4)/`FAILURES`(1)/`CHALLENGES`(5)
- **app/main.py**: `_leading_zero_bits()`·`pow_verify()` 헬퍼 · `VerifyRequest.pow_nonce` · `create_challenge`에서 세션 위험도로 난이도 산정(17→21) & 응답 `pow:{seed,bits}` · `verify`에서 챌린지별 저장비트로 PoW 게이트(정답/행동 채점 이전)
- **app/db.py**: `captcha_challenges_v2.pow_bits` 컬럼 · `create_challenge` INSERT에 `pow_bits` · `connection()` 정상종료 auto-commit
- **src/main.jsx**: SHA-256 Web Worker `solvePow` · `load()`에서 `solvePow(row.pow)` 시작 · `verify()`에서 `pow_nonce` 첨부 → 빌드 재생성 필요
- ⚠️ **프론트 워커 필수**: 안 넣으면 모든 verify가 `pow_failed`(급하면 `POW_ENABLED=0`)

## 2. 봇차단 강화 — 허니팟 + 드롭존 랜덤화 (커밋 `d0fbe60`, 드래그 방식 유지)
- **허니팟**(서버 위주): `create_challenge`가 빈 영역에 투명 함정 히트영역 추가(`tmp_` 형식이라 실객체와 구분 불가). `verify`에서 제출 시 즉시 차단(`reason=honeypot`). `captcha_challenges_v2.honeypot_ids` 컬럼 · `HONEYPOT_COUNT`(1). `_place_honeypot()` 헬퍼.
- **드롭존 랜덤화**: `create_challenge`가 `drop_zone` x/width 랜덤화 → `src/main.jsx`의 `cc-zone`이 그 좌표로 배치(옵셔널체이닝 주의: `challenge?.drop_zone`).

## 3. behavior-AI (커밋 `09c5351` 브릿지 + `c0502f4` 승격 준비)
> sw-captcha는 이미 behavior-AI 연동이 있으므로 **중복 주의**. 아래 중 sw-captcha에 없는 것만.
- **브릿지**(`app/behavior_client.py`): 모델 서비스 HTTP 클라이언트 + `verify`에서 shadow 채점. `BEHAVIOR_AI_URL`/`KEY`/`TIMEOUT`, `BEHAVIOR_POLICY_MODE`(shadow). (sw-captcha에 이미 있으면 skip)
- **승격 준비도 엔드포인트**(`/api/admin/behavior-shadow`): shadow 예측 집계로 active 승격 go/no-go. `BEHAVIOR_PROMOTE_MIN_PASSED`(500)/`MAX_FP_RATE`(0.02). `db.behavior_shadow_summary()`.
- **런북**: `docs/BEHAVIOR_AI_PROMOTION.md` — 2단계(캡차+모델) 승격·카나리·롤백.

## 4. 선택 — 전체 방어 스택 (sw-captcha에 없을 때만, diff 먼저 확인)
- `5453932` 문항 회전(+ `served_count`/`last_served_at` 컬럼, `/api/admin/exposure`·`/rest`) · `eab405e` 자동화 탐지(`automation_score`) · `c7d9274` Origin 검증(`check_origin`)+클러스터 · `fa1d1d3` 레이트리밋 미들웨어 · `21bea0e` 클러스터 탐지(`/api/admin/clusters`) · `32d086c` 1분 제한시간

---

## 5. 병합 주의
- **`create_challenge`/`verify`는 통짜 cherry-pick 위험** — sw-captcha와 구조가 다름(배치채널). **로직 이식** 권장. PoW·허니팟·드롭존이 모두 이 두 함수를 건드림.
- **모델 키는 공유 `.env`에 이미 정렬됨**(`BEHAVIOR_AI_URL`=http://127.0.0.1:8010 + `BEHAVIOR_AI_BACKEND_KEY`=sw 64자). sw-captcha가 같은 `.env`면 그대로 연동.
- 정확한 diff: `git fetch && git show <커밋>`.

## 6. 이미 적용된 공유 DB 마이그레이션 (`captcha_ms`, 재적용 불요)
DEFAULT 있어 타 빌드 무영향. `SELECT *`면 자동 반영.
- `captcha_challenges_v2`: `lecture_id`, `pow_bits`, `honeypot_ids`
- `captcha_tokens`: `lecture_id`
- `captcha_questions`: `served_count`, `last_served_at`
- (읽기 전용) `behavior_shadow_predictions`는 모델 서비스가 적재

## 7. 새 env 변수 요약
`POW_ENABLED`·`POW_DIFFICULTY_BITS`·`POW_STEPUP_BITS/FAILURES/CHALLENGES` · `HONEYPOT_COUNT` · `BEHAVIOR_AI_URL/BACKEND_KEY/TIMEOUT_SECONDS`·`BEHAVIOR_POLICY_MODE`·`BEHAVIOR_DEBUG_RESPONSE`·`BEHAVIOR_PROMOTE_MIN_PASSED/MAX_FP_RATE` · `ROTATION_COOLDOWN_SECONDS`·`RATE_LIMIT_PER_MINUTE`·`CLUSTER_BLOCK_SIZE/WINDOW_HOURS`·`BEHAVIOR_STEP_UP_SCORE/BLOCK_SCORE`

## 8. 새 관리자 엔드포인트
`/api/admin/exposure`(노출상위)·`/api/admin/rest/{id}`(문항 내림)·`/api/admin/clusters`(클러스터)·`/api/admin/behavior-shadow`(승격 준비도)

---

## 9. 배포 & 검증
1. sw-captcha 병합 → `npm run build`(PoW 워커·드롭존 포함 재빌드)
2. sw-captcha를 GPU `:8000`에 배포 (현 A=`/srv/codex-workspaces/ms/drag-captcha` 대체)
3. 검증:
   - `challenge` 응답에 `pow:{seed,bits}` + `drop_zone` 랜덤 + 허니팟 포함(objects 수 > 실객체)
   - 정상 풀이(정답+PoW) → 통과
   - PoW 미해결 → `{"pow_failed":true}` / 허니팟 제출 → `{"blocked":true,"reason":"honeypot"}`
   - 과다요청 세션 6회째 `pow.bits`=21(적응형)
   - **UI 정상 렌더**(초기 challenge=null 렌더 확인 — `challenge?.drop_zone`)
4. 배포 후 스테이징 `/srv/codex-workspaces/ms/dcb` 제거

## 아키텍처 참고
- GPU(`61.109.239.231`, ms 계정) = 캡차 백엔드 · 210(`210.109.52.114`) = DB(`captcha_ms`) · 모델 = GPU `127.0.0.1:8010`(sw, LightGBM, shadow)
- 출제: `status='active' AND review_status='approved'`. 현재 1,660개(사람 검수 전량).
- 미완(실서비스 직전): 시크릿/관리자키 로테이션 · ALLOWED_ORIGINS 도메인 잠금 · 방화벽 8000 개방 · behavior-AI active 승격(데이터 대기)
