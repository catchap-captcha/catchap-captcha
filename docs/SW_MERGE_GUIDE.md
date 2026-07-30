# sw-captcha ← ms 병합 가이드

정본은 **sw-captcha**(sw 유지). 이 저장소(`catchap-captcha/ai-service`, `origin/ms`)의 방어 기능을 sw-captcha로 가져가기 위한 안내.

## 현황
| | sw-captcha (정본) | ms (이 저장소) |
|---|---|---|
| 배치채널(영수증체인)·behavior-AI·rich health | ✅ 있음 | ❌ 없음 |
| **PoW / 적응형 PoW** | ❌ 없음 | ✅ 있음 |
| 클러스터·레이트리밋·자동화탐지·Origin·문항회전 | ? (diff로 확인) | ✅ 있음 |

목표: **최소 PoW(+적응형)** 를 sw-captcha에 얹는 것. 나머지는 sw-captcha에 없을 때만.

---

## 1. 필수 — PoW (커밋 2개)

### `e59c86c` Proof-of-Work 기반
- **app/config.py**: `POW_ENABLED`, `POW_DIFFICULTY_BITS`(기본 17)
- **app/main.py**: `_leading_zero_bits()`, `pow_verify(seed,nonce,bits)` 헬퍼 · `VerifyRequest.pow_nonce` 필드 · `create_challenge` 응답에 `pow:{seed,bits}` · `verify`에서 PoW 게이트(정답/행동 채점 이전)
- **src/main.jsx**: SHA-256 Web Worker + `solvePow()` · `load()`에서 `solvePow(row.pow)` 시작 · `verify()`에서 `pow_nonce` 첨부
- **static/dist**: 빌드 산출물 → 병합 후 `npm run build`로 재생성

### `20aefe1` 적응형 PoW + connection 하드닝
- **app/config.py**: `POW_STEPUP_BITS`(4)·`POW_STEPUP_FAILURES`(1)·`POW_STEPUP_CHALLENGES`(5)
- **app/db.py**: `captcha_challenges_v2.pow_bits` 컬럼(멱등 마이그레이션) · `create_challenge` INSERT에 `pow_bits` · `connection()` 정상종료 시 auto-commit
- **app/main.py**: `create_challenge`에서 세션 위험도로 난이도 산정(17→+4=21) · `verify`에서 챌린지별 저장 비트로 검증

> 정확한 diff: `git fetch && git show e59c86c` / `git show 20aefe1`

---

## 2. 병합 시 주의

- **create_challenge/verify는 sw-captcha와 구조가 다름**(배치채널 등). 커밋 통짜 cherry-pick보다 **로직 이식** 권장. 두 커밋 모두 이 두 함수를 건드리므로 수동 병합 필요.
- **프론트 필수**: PoW는 클라이언트가 nonce를 풀어야 통과. sw-captcha 프론트에 `solvePow` 워커를 넣고 `pow.bits`를 풀어 `verify`에 `pow_nonce`로 보내야 함. 안 넣으면 모든 verify가 `pow_failed`(→ 급하면 `POW_ENABLED=0`로 끌 수 있음).
- **모델 키는 공유 `.env`에 이미 정렬됨**(`BEHAVIOR_AI_URL`=http://127.0.0.1:8010, `BEHAVIOR_AI_BACKEND_KEY`=sw의 64자 키). sw-captcha가 같은 `.env`를 쓰면 그대로 연동.

---

## 3. 이미 적용된 공유 DB 마이그레이션 (재적용 불요)
`captcha_ms`(210)에 이미 적용됨. DEFAULT 값이 있어 기존/타 빌드 무영향. sw-captcha가 `SELECT *`로 읽으면 자동 반영, 명시 컬럼 SELECT면 추가만 하면 됨.
- `captcha_challenges_v2`: `lecture_id`, `pow_bits`, `honeypot_ids`
- `captcha_tokens`: `lecture_id`
- `captcha_questions`: `served_count`, `last_served_at`

---

## 4. 선택 — 전체 방어 스택 (sw-captcha에 없을 때만)
- `d0fbe60` **허니팟 객체 + 드롭존 랜덤화** (드래그 방식 유지, 봇차단↑) — 서버 위주, 프론트는 드롭존 위치 반영 1줄
- `09c61fb` 행동점수 강화 · `21bea0e` 클러스터 탐지 · `fa1d1d3` 레이트리밋 · `c7d9274` Origin 검증 · `eab405e` 자동화 탐지 · `5453932` 문항 회전 · `32d086c` 1분 제한시간
- **중복 병합 금지** — 먼저 diff로 sw-captcha가 이미 가진 것 확인.

---

## 5. 배포 & 검증
1. sw-captcha에 병합 → `npm run build`(PoW 워커 포함 재빌드)
2. sw-captcha를 GPU `:8000`에 배포 (현 A=`/srv/codex-workspaces/ms/drag-captcha` 대체)
3. 검증:
   - `challenge` 응답에 `pow:{seed,bits}` 존재
   - 정상 풀이(정답+PoW) → 통과
   - PoW 미해결 verify → `{"pow_failed":true}`
   - 과다요청 세션 6회째 challenge의 `pow.bits`가 21로 상승(적응형)
4. 배포 후 스테이징 `/srv/codex-workspaces/ms/dcb` 제거

## 아키텍처 참고
- GPU(`61.109.239.231`, ms 계정) = 캡차 백엔드 · 210(`210.109.52.114`) = DB(`captcha_ms`) · 모델 = GPU `127.0.0.1:8010`(sw, LightGBM, shadow)
- 출제 조건: `status='active' AND review_status='approved'`. 현재 1,660개(사람 검수 전량).
