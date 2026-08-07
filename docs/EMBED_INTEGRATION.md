# 캡차 플랫폼 연동 사양 (지영/인프라 핸드오프)

`www.catchap5.com`(HTTPS) 플랫폼에 캡차 위젯을 붙이는 사양. 확정안 = **전용 호스트 방식**.
캡차 측(ms) 준비는 끝났고, 아래 **A(인프라)·B(백엔드)** 가 지영님 몫.

---

## 전제 — 왜 전용 호스트인가
- 플랫폼이 HTTPS라 평문 캡차(`http://…:8000`)를 그냥 붙이면 **mixed content로 차단**.
- 하위경로 프록시(`/captcha-api/*`)는 index.html asset 경로·번들 API 호출·widget.js origin이
  전부 **루트 절대경로**라 프론트 재빌드 3곳 필요 → 비효율.
- **캡차에 전용 호스트를 루트로** 주면 재빌드 0으로 전부 해결.

---

## A. 인프라 — `captcha.catchap5.com` HTTPS 프록시
```
https://captcha.catchap5.com/*   →   http://61.109.239.231:8000/*   (루트 그대로, 전 경로 통과)
```
- **DNS**: `captcha.catchap5.com` A레코드 → `61.109.239.231` (또는 내부망이면 `10.0.1.52`)
- **HTTPS 리버스 프록시**(nginx/Caddy 등) + TLS 인증서(Let's Encrypt). 경로 변형 없이 그대로 전달:
  `/`, `/assets/*`, `/widget.js`, `/api/*`, `/admin` 전부.
- 캡차 측은 이미 **`EMBED_ORIGINS=https://www.catchap5.com`** 설정됨 → 응답 헤더가
  `frame-ancestors 'self' https://www.catchap5.com` 라 플랫폼에서 iframe 통과.
- (참고) 이 호스트가 HTTPS면 `/admin` 검수 콘솔도 https로 접근 가능.

## B. 백엔드 — 토큰 서버-대-서버 검증
사용자가 캡차를 통과하면 위젯이 플랫폼에 **token·session_id·purpose**를 넘긴다.
플랫폼 **백엔드**가 그걸 캡차로 재검증한다(브라우저가 아니라 서버끼리).

```
POST https://captcha.catchap5.com/api/verify-token
Header: X-Captcha-Site-Secret: <CAPTCHA_SITE_SECRET>
Body:   { "token": "...", "session_id": "...", "purpose": "lecture", "lecture_id": "LEC-..." }

→ 성공: { "success": true, "lecture_id": "...", "challenge_id": "..." }
→ 실패: { "success": false, "error": "invalid_or_used_token" }
```
- ⚠️ **`session_id`·`purpose` 필수** — 토큰이 그 세션에 묶여 있어, 안 맞으면 `invalid_or_used_token`.
  (플랫폼이 임의 세션 ID를 쓰면 안 됨. **위젯이 준 값을 그대로** 넘겨야 함.)
- ⚠️ **1회용** — 한 번 검증하면 소비됨. 재호출 시 실패.
- **토큰 수명**: 응답의 `expires_in`(현재 300초)을 **그대로 사용**. 하드코딩 금지(값 바뀌어도 안 깨지게).
- **`CAPTCHA_SITE_SECRET`**: GPU `/tmp/catchap_site_secret.txt` (sw·jy 읽기권한, 채팅 미노출).
  실서비스 전 로테이션 예정이니 파일에서 읽어 쓰기.

---

## 데이터 흐름 (전체)
```
[플랫폼 페이지]  위젯 스니펫 로드
      │  widget.js가 iframe(https://captcha.catchap5.com/?embed=1) 삽입
[사용자]  캡차 풀이 → 통과
      │  캡차가 postMessage로 { token, session_id, purpose, lecture_id } 전달
[플랫폼 프론트]  window.catchapOnVerified(token, ctx) 호출  (ctx={lectureId,sessionId,purpose})
      │  token·ctx.sessionId·ctx.purpose·ctx.lectureId 를 플랫폼 백엔드로 POST
[플랫폼 백엔드]  POST /api/verify-token (X-Captcha-Site-Secret)  → success 확인 후 통과 처리
```

## 플랫폼 프론트 스니펫 (참고 — 프론트 담당)
```html
<div id="catchap"></div>
<script>
  window.catchapOnVerified = function (token, ctx) {   // ctx = {lectureId, sessionId, purpose}
    fetch("/플랫폼백엔드/captcha-verify", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: token, session_id: ctx.sessionId,
                             purpose: ctx.purpose, lecture_id: ctx.lectureId })
    });
  };
</script>
<script src="https://captcha.catchap5.com/widget.js"
        data-target="#catchap" data-lecture-id="LEC-2026-01"></script>
```

## 담당 구분
| 파트 | 담당 |
|---|---|
| A. `captcha.catchap5.com` DNS + HTTPS 프록시 | **인프라(지영)** |
| B. `/api/verify-token` 백엔드 호출 | **백엔드(지영)** |
| 스니펫 삽입 + `catchapOnVerified` | 플랫폼 프론트 |
| 위젯·캡차 서버 (EMBED_ORIGINS·widget.js·postMessage session_id) | **ms — 완료** |

## ms 측 완료 상태
- ✅ `EMBED_ORIGINS=https://www.catchap5.com` 설정·재시작 (frame-ancestors·postMessage 허용)
- ✅ 위젯 postMessage에 `session_id`·`purpose` 실음 + widget.js 콜백이 `ctx`로 전달 (배포는 위 A/B 준비되면 함께)
- ✅ 토큰 수명 300 (응답 `expires_in`으로 노출)
