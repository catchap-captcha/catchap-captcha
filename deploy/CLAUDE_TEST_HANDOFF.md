# CatChap 행동 AI 테스트 인수인계서

## 역할과 제한

당신의 역할은 **테스트 및 결과 기록만**이다.

- 소스 코드, 환경 파일, 모델 파일, DB 스키마를 수정하지 않는다.
- Git 명령어(`commit`, `push`, `merge`, `reset` 등)를 실행하지 않는다.
- 서버 배포, 포트 공개, 방화벽 변경을 하지 않는다.
- 실제 외부 CAPTCHA나 제3자 서비스는 대상으로 삼지 않는다.
- 실행 결과와 재현 절차만 문서로 남긴다.

## 현재 구조

| 구성요소 | 주소 | 역할 |
| --- | --- | --- |
| 메인 CAPTCHA API | `127.0.0.1:8000` | 문제 발급·정답 검증·행동 이벤트 수신 |
| 행동 AI API | `127.0.0.1:8010` | 행동 궤적 점수화·shadow 결과 저장 |
| 프론트엔드 | 메인 CAPTCHA API가 제공 | 사용자 드래그 이벤트 전송 |

브라우저는 행동 AI API를 직접 호출하면 안 된다. 메인 CAPTCHA API만
`BEHAVIOR_AI_URL=http://127.0.0.1:8010`으로 행동 AI를 호출한다.

## 반드시 유지할 설정

```env
BEHAVIOR_EVENT_TRANSPORT=active
BEHAVIOR_POLICY_MODE=shadow
RISK_POLICY_MODE=shadow
BEHAVIOR_DEBUG_RESPONSE=false
```

shadow 모드에서는 AI가 `step_up` 또는 `step_up_and_rate_limit`을 권장해도
정답 CAPTCHA의 통과 결과를 바꾸면 안 된다.

## 사전 확인

1. 행동 AI API의 `GET /health`를 확인한다.
2. 메인 CAPTCHA API의 `GET /health/ready`를 확인한다.
3. 두 API가 모두 준비되기 전에는 브라우저 테스트를 진행하지 않는다.

### 성공 기준

행동 AI `/health`:

```json
{
  "status": "ok",
  "model_loaded": true,
  "policy_mode": "shadow"
}
```

메인 CAPTCHA `/health/ready`:

```json
{
  "status": "ok",
  "database_ready": true,
  "approved_questions": true,
  "behavior_policy_mode": "shadow",
  "behavior_event_transport": "active",
  "behavior_ai_policy_matches": true,
  "behavior_ai": {
    "ready": true,
    "model_loaded": true,
    "policy_mode": "shadow"
  }
}
```

## 테스트 항목

### 1. 기본 연동

1. 브라우저로 CAPTCHA 문제를 발급한다.
2. 객체를 자연스럽게 드래그하여 정답을 제출한다.
3. 메인 CAPTCHA 응답에서 정답 결과가 정상인지 확인한다.
4. 메인 API 로그 또는 DB에서 행동 이벤트 배치 수신 여부를 확인한다.
5. 행동 AI DB에서 동일 `attempt_id`의 점수와 shadow 결과가 저장됐는지 확인한다.

기록할 값: `challenge_id`, `attempt_id`, CAPTCHA 정답 결과, AI 점수,
위험도, 권장 조치, 모델 이름·버전, shadow 결과 저장 여부.

### 2. Shadow 무영향성

행동 AI가 중간 또는 높은 위험도를 반환하는 테스트 데이터가 있을 때 아래를 확인한다.

1. 정답 CAPTCHA 제출 결과가 `passed`인지 확인한다.
2. AI 권장 조치가 `step_up` 또는 `step_up_and_rate_limit`인지 확인한다.
3. 최종 CAPTCHA 결과가 AI 권장 조치 때문에 `failed`로 바뀌지 않았는지 확인한다.
4. shadow 결과에 메인 CAPTCHA의 실제 결과와 AI의 권장 조치가 함께 저장됐는지 확인한다.

### 3. 실패 안전성

테스트용 환경에서만 행동 AI API를 중지하거나 URL을 임시로 잘못 지정해 확인한다.

1. `BEHAVIOR_EVENT_TRANSPORT=active` 상태에서 메인 `/health/ready`가
   `status: error`인지 확인한다.
2. `BEHAVIOR_EVENT_TRANSPORT=off`로 바꾸면 메인 CAPTCHA 기본 기능만으로
   준비 상태가 정상으로 돌아오는지 확인한다.
3. 설정을 원래 `active`와 `shadow`로 되돌린 뒤, AI API 재연결 후 준비 상태가
   다시 정상인지 확인한다.

### 4. 이벤트 무결성

메인 CAPTCHA API가 아래 요청을 거부하는지 확인한다.

- 행동 이벤트 없이 정답만 제출
- 만료되었거나 다른 challenge의 이벤트 배치 제출
- 동일 객체를 중복 선택한 결과 제출
- 객체 영역 밖에서 시작한 드래그 제출
- 이벤트 순서가 맞지 않는 제출

기록할 값: HTTP 상태 코드, 오류 메시지, CAPTCHA 결과, AI 호출 여부.

## 테스트 결과 문서 형식

아래 표를 채운다. 비밀값, 정답 객체 ID, 원시 사용자 궤적은 기록하지 않는다.

| 일시 | 환경 | 항목 | 결과 | 핵심 관측값 | 문제 여부 |
| --- | --- | --- | --- | --- | --- |
|  | local/server | 기본 연동 | pass/fail | risk, action, shadow 저장 여부 |  |

문제가 있으면 아래도 기록한다.

```text
재현 절차:
기대 결과:
실제 결과:
HTTP 상태 코드 또는 로그 요약:
영향 범위:
```

## 현재 알고 있는 한계

- VAE 기반 레드팀 데이터의 최근 합산 우회율은 `5.67%`다.
- 따라서 모델 기반 차단을 켜지 않고 shadow 모드로만 검증한다.
- 실제 대표 사용자 데이터가 충분하지 않으므로, 테스트 결과는 운영 배포 전 검증 자료이지
  모델 차단 승격 근거가 아니다.
