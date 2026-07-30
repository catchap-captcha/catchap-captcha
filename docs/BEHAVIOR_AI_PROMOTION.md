# behavior-AI shadow → active 승격 런북

behavior-AI(LightGBM)를 **shadow(기록만) → active(실차단)**로 올리는 절차. 잘못하면 **사람을 차단**하므로 데이터 기반으로만 진행.

## 핵심: 승격은 2단계(양쪽) 스위치
active 차단이 실제로 걸리려면 **둘 다 active**여야 함(`resolve_final_verdict`):
1. **캡차 쪽**: `.env`의 `BEHAVIOR_POLICY_MODE=active`
2. **모델 서비스 쪽(sw)**: 예측의 `policy_mode`가 `active`. **현재는 candidate라 항상 shadow 반환** → sw가 모델을 정식 승격해야 함.

둘 중 하나라도 shadow면 → 통과(차단 안 함). 안전장치.

## 승격 전 준비도 판단 (도구 내장)
```
GET /api/admin/behavior-shadow?days=7   (X-Captcha-Admin-Key 필요)
```
반환: 예측 표본 수, `passed`(정답 통과=사람 프록시), `fp_proxy_rate`(사람 프록시 중 모델이 step_up할 비율=오탐 프록시), `verdict`.

**go/no-go 기준**(`.env`로 조절):
- `BEHAVIOR_PROMOTE_MIN_PASSED`(기본 500): 사람 프록시 표본 최소치
- `BEHAVIOR_PROMOTE_MAX_FP_RATE`(기본 0.02=2%): 허용 오탐 프록시율
- `verdict: "ready"`가 떠야 승격 검토.

> 2026-07-30 현재: 표본 12 → `insufficient_data`. **실사용자 트래픽이 없어(방화벽 뒤) 데이터가 안 쌓임.** 공개 개방/베타로 실트래픽이 흘러야 판단 가능.

## 승격 절차 (ready 떴을 때)
1. **데이터 확인**: `/api/admin/behavior-shadow?days=14` → `verdict: ready`, `fp_proxy_rate` 허용 이하 확인
2. **sw: 모델 candidate→active 승격** (모델 서비스가 `policy_mode: active` 반환하도록). 모델 버전 고정.
3. **카나리**: 처음엔 일부 트래픽만 active. (권장 구현: `BEHAVIOR_POLICY_MODE=active`를 세션 해시 % N < K 인 경우만 적용하는 캐너리 게이트 — 미구현, 필요 시 추가)
4. **모니터링**: 승격 후 실제 차단율·문의(사람 차단 항의) 급증 감시. `/api/admin/behavior-shadow`로 지속 확인.
5. **전면 확대**: 카나리 지표 정상이면 전량 active.

## 롤백 (즉시)
- 캡차: `.env` `BEHAVIOR_POLICY_MODE=shadow` → `systemctl --user restart drag-captcha.service`. 끝(즉시 차단 중단).
- 또는 sw가 모델 policy_mode를 shadow로.
- fail-safe: 모델 오류/미도달 시엔 shadow처럼 통과(이미 검증됨).

## 참고 상태(2026-07-30)
- 모델: GPU `127.0.0.1:8010`, LightGBM `lightgbm_general_dynamics_min_fusion`, **shadow**. 캡차와 키 연동 완료(`/predict 200`).
- 데이터 수집: 라이브 A(배치채널 보유)가 shadow 예측/배치를 `captcha_ms`에 적재 중.
- 준비도 엔드포인트: `/api/admin/behavior-shadow` (ms 브랜치, sw 병합 시 함께).
