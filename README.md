# CatChap Security CAPTCHA

Visual Genome/TallyQA 기반의 다중 객체 직접 드래그 CAPTCHA입니다. 관리자 라벨링, 일회성 문제 발급·검증, 회원가입 토큰 소비, 행동 데이터 수집을 하나의 수직 흐름으로 제공합니다.

## 데이터 저장

- 문제 메타데이터와 bbox: MySQL `captcha_questions`, `captcha_objects`
- 발급·시도·행동 요약·토큰: MySQL `captcha_challenges_v2`, `captcha_attempts`, `behavior_summaries`, `captcha_tokens`
- 행동 AI shadow 결과: MySQL `behavior_shadow_predictions`
- 서버 검증 행동 배치: MySQL `captcha_behavior_sessions`, `captcha_behavior_batches`
- 최종 이미지·조각: `data/final/images`, `data/final/pieces`
- 라벨링 큐·결과: `data/labeling`
- 원시 행동 이벤트: `data/runtime/behavior-events/YYYY/MM/DD`

정답 역할과 원본 객체 ID는 공개 API 응답에 포함되지 않습니다. 문제별 객체 ID는 임시 ID로 치환됩니다.

## 실행

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
npm install
npm run build
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

로컬에서 `npm run dev`를 실행하면 SSH 터널을 자동으로 열고 서버의 FastAPI와
연결합니다. 따라서 로컬에 별도로 백엔드를 실행할 필요가 없습니다. SSH 키가
기본 경로와 다르면 `CAPTCHA_SSH_KEY`를 지정합니다. UI만 실행하는 명령은
`npm run dev:ui`이며, 이 경우 `127.0.0.1:18000`에 별도 API 터널이 있어야 합니다.

- 사용자 CAPTCHA 및 회원가입: `/`
- 관리자 라벨링 콘솔: `/admin`
- API 문서: `/docs`
- 준비 상태: `/health/ready`

## 라벨링 큐 생성

이미 다운로드된 TallyQA, Visual Genome 이미지·객체·관계·메타데이터 ZIP을 직접 읽고 필요한 후보 이미지만 추출합니다.

```bash
.venv/bin/python scripts/build_labeling_queue.py --limit 100
```

후보의 bbox는 자동 연결하지만 `target` 여부는 확정하지 않습니다. 관리자가 `/admin`에서 `target`, `decoy`, `ambiguous`, `invalid`를 지정합니다. 승인 시 이미지와 객체 조각을 `data/final`로 복사하고 DB에 활성 문제로 등록합니다.

승인 조건은 target 수와 TallyQA 정답 수가 같고 ambiguous 객체가 없는 것입니다.

## API 흐름

1. `POST /api/captcha/challenges`
2. 객체를 정답존에 드래그
3. 활성화 시 200ms 이하 단위로 `POST /api/captcha/challenges/{id}/behavior-batches`
4. `POST /api/captcha/challenges/{id}/verify`
5. 성공 시 목적·세션에 묶인 1회용 토큰 발급
6. `POST /api/signup`에서 토큰 소비 후 계정 생성

원시 포인터 이벤트는 파일로, 행동 요약과 AI shadow 결과는 MySQL로 분리 저장합니다.

## 행동 AI shadow 연동

메인 CAPTCHA 서버는 정답을 직접 판정하고, 브라우저가 보낸 드래그 이벤트를 서버 간 호출로
`sw` 행동 AI의 `POST /api/v1/behavior/predict`에 전달합니다. 정답 ID와 사용자가 고른 답은
행동 AI에 보내지 않습니다.

기본값 `BEHAVIOR_POLICY_MODE=shadow`에서는 행동 AI가 `step_up` 또는 `block`을 권고해도
CAPTCHA의 통과/실패와 토큰 발급은 바뀌지 않습니다. 두 서비스의 shadow 결과가 각각 저장되어
정상 사용자 오탐률을 먼저 확인할 수 있습니다.

서버 환경 변수는 다음처럼 맞춥니다.

```bash
# ms CAPTCHA service
BEHAVIOR_AI_URL=http://127.0.0.1:8010
BEHAVIOR_AI_BACKEND_KEY=<sw의 CAPTCHA_BACKEND_API_KEY와 동일한 값>
BEHAVIOR_POLICY_MODE=shadow

# sw behavior service
CAPTCHA_BACKEND_API_KEY=<동일한 비밀값>
RISK_POLICY_MODE=shadow
PRODUCTION_MODEL_DIR=/srv/catchap-behavior/models/candidate/revalidation_two_view_participant_safe_20260722
```

`BEHAVIOR_POLICY_MODE=active`와 `RISK_POLICY_MODE=active`가 **둘 다** 설정되기 전에는 AI 추천이
캡차 결과를 바꾸지 않습니다. 활성화 전에는 메인 캡차 실제 궤적으로 임계값을 재보정해야 합니다.

### 새 VPC 배포용 이벤트 전송 모드

`BEHAVIOR_EVENT_TRANSPORT`는 AI 모델 정책과 별개로 브라우저 이벤트의 전송 방식을 정합니다.

- `off` (기본): 기존 CAPTCHA만 동작합니다. 테스트 서버나 아직 통합되지 않은 프론트에 안전합니다.
- `shadow`: challenge별 nonce, 이벤트 순번, 이전 배치 영수증 해시를 사용해 이벤트를 서버에 저장하고 AI 점수만 기록합니다. CAPTCHA 통과 여부는 바꾸지 않습니다.
- `active`: `shadow`와 같은 서버 수집을 사용하며, 누락되거나 무결성이 깨진 행동 데이터는 `step_up`으로 처리합니다.

새 프라이빗 VPC에서는 `shadow`로 먼저 올립니다. CAPTCHA 서버는 브라우저가 접근하는 공개 진입점 뒤에 두고, `BEHAVIOR_AI_URL`과 MySQL은 VPC 내부 주소만 사용합니다. 준비 확인은 다음 순서로 합니다.

```bash
npm ci && npm run build
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
BEHAVIOR_EVENT_TRANSPORT=shadow .venv/bin/python scripts/smoke_behavior_batches.py
```

위 smoke test가 통과한 뒤 실제 사용자 shadow 로그로 오탐률을 확인하고, 그 다음에만 `BEHAVIOR_EVENT_TRANSPORT=active`와 양쪽 AI 정책 `active`를 함께 검토합니다.

## 운영 보안

- `.env`의 앱 비밀키, 사이트 비밀키, 관리자 키를 충분히 긴 난수로 설정합니다.
- `ALLOWED_ORIGINS`를 실제 연동 도메인으로 제한합니다.
- HTTPS 리버스 프록시 뒤에서 실행하고 `TRUST_PROXY=true`를 적용합니다.
- 관리자 라벨링 페이지는 별도 네트워크 접근제어 또는 SSO 뒤에 두는 것을 권장합니다.
