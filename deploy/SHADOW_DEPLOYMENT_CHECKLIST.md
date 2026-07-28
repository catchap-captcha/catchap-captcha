# CatChap 행동 AI Shadow 배포 체크리스트

## 목적

기존 객체 드래그 CAPTCHA와 행동 탐지기를 별도 서비스로 운영한다. 브라우저는
CAPTCHA API와만 통신하고, CAPTCHA API가 서버 내부 주소와 백엔드 키를 이용해
행동 AI 서비스에 요청한다.

현재 단계는 **shadow 모드**다. CAPTCHA 정답이 최종 결과를 결정하며, 행동 점수와
권장 조치, 최종 결과는 분석을 위해 기록만 한다. 행동 AI의 권장 조치가 정답 CAPTCHA
통과 여부를 바꾸지는 않는다.

## 서비스 구성

| 서비스 | 바인딩 주소 | 역할 |
| --- | --- | --- |
| 리버스 프록시 | 공개 HTTPS | CAPTCHA만 외부에 제공 |
| 메인 CAPTCHA API | `127.0.0.1:8000` | 문제 발급·검증, 행동 이벤트 배치 검증 |
| 행동 AI API | `127.0.0.1:8010` | 신뢰된 이벤트를 점수화하고 shadow 결과 기록 |
| MySQL | 사설 네트워크 | CAPTCHA 상태 및 행동 AI 기록 저장 |

`8010` 포트, 행동 AI API 문서, 모델 추론 API, 백엔드 키는 브라우저나 인터넷에
공개하지 않는다.

## 필수 설정

행동 AI 서비스는 현재의 two-view 후보 모델 번들로 설정한다.

```env
CAPTCHA_BACKEND_API_KEY=<길고_무작위인_공유_비밀키>
PRODUCTION_MODEL_DIR=models/candidate/revalidation_two_view_participant_safe_20260722
RISK_POLICY_MODE=shadow
MYSQL_HOST=<사설_MySQL_호스트>
MYSQL_PORT=3306
MYSQL_DATABASE=<행동_AI_데이터베이스>
MYSQL_USER=<행동_AI_계정>
MYSQL_PASSWORD=<데이터베이스_비밀번호>
```

메인 CAPTCHA 서비스는 아래와 같이 설정한다.

```env
APP_ENV=production
ALLOWED_ORIGINS=https://<실제_CAPTCHA_도메인>
TRUST_PROXY=true
BEHAVIOR_EVENT_TRANSPORT=active
BEHAVIOR_POLICY_MODE=shadow
BEHAVIOR_AI_URL=http://127.0.0.1:8010
BEHAVIOR_AI_BACKEND_KEY=<위와_동일한_공유_비밀키>
BEHAVIOR_DEBUG_RESPONSE=false
```

`APP_SECRET`, `CAPTCHA_SITE_SECRET`, `CAPTCHA_ADMIN_KEY`에는 운영 전용 값을
설정한다. 모든 비밀값은 systemd 환경 파일 또는 시크릿 관리 도구에만 보관하고,
저장소에는 넣지 않는다.

## 실행 순서 및 확인 기준

1. 행동 AI 서비스의 MySQL 스키마를 적용하고, 행동 AI 데이터베이스 계정에 쓰기
   권한이 있는지 확인한다.
2. 설정한 모델 디렉터리로 행동 AI API를 `127.0.0.1:8010`에서 실행한다.
3. 서버 내부에서 `http://127.0.0.1:8010/health`를 요청한다. 응답은
   `status: ok`, `model_loaded: true`, `policy_mode: shadow`여야 한다.
4. 이벤트 전송을 active로 설정한 메인 CAPTCHA API를 `127.0.0.1:8000`에서 실행한다.
5. `http://127.0.0.1:8000/health/ready`를 요청한다. 응답은 `status: ok`,
   `database_ready: true`, `approved_questions: true`, `behavior_ai.ready: true`,
   `behavior_ai_policy_matches: true`여야 한다.
6. 실제 브라우저에서 CAPTCHA를 한 번 푼다. 행동 이벤트 배치가 저장되고, 행동 AI가
   점수를 생성하며, shadow 결과가 기록되는지 확인한다.
7. 행동 AI 권장 조치가 `step_up` 또는 `step_up_and_rate_limit`이어도, CAPTCHA
   정답이 맞으면 통과하는지 확인한다.

행동 이벤트 전송이 active인데 AI API에 연결할 수 없거나, AI가 degraded 상태이거나,
모델이 로드되지 않았으면 준비 상태 API는 의도적으로 `error`를 반환한다. 일부만
배포된 상태가 정상처럼 보이는 것을 막기 위해서다.

## 운영 환경 설정

- 리버스 프록시에서 TLS를 종료하고 HTTP 요청은 HTTPS로 리다이렉트한다.
- `X-Forwarded-For`, `X-Forwarded-Proto` 헤더를 전달한다. `TRUST_PROXY=true`는
  신뢰할 수 있는 프록시 뒤에서만 사용한다.
- `ALLOWED_ORIGINS`에는 `*` 대신 실제 웹 Origin을 정확히 설정한다.
- API 로그에 백엔드 키, CAPTCHA 정답, 사용자 식별 가능 정보가 남지 않게 한다.
- MySQL과 `8010` 포트는 애플리케이션 서버 또는 사설 VPC에서만 접근 가능하게 제한한다.

## 롤백 및 장애 대응

배포 뒤 행동 AI 서비스를 사용할 수 없으면 `BEHAVIOR_EVENT_TRANSPORT=off`로 바꾸고
메인 CAPTCHA 서비스를 재시작한다. 그러면 행동 데이터 수집만 중단되고 일반 CAPTCHA
흐름은 계속 동작한다.

새롭고 대표성 있는 실제 서비스 데이터로 별도의 승격 기준을 만족하기 전까지는
`BEHAVIOR_POLICY_MODE`를 `shadow`에서 `active`로 바꾸지 않는다.

## 현재 한계

최근 VAE 기반 레드팀 동작 데이터의 합산 모델 우회율은 `5.67%`였다. 아직 5% 이하로
안정적으로 유지되는 수준이 아니므로 모델 차단을 켜면 안 된다. 행동 판단은 shadow
모드로 유지하고, 동의를 받은 실제 서비스 행동 데이터로 임계값을 다시 검증한 뒤에만
active 전환을 검토한다.
