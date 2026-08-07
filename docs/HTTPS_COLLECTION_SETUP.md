# 수집 창 HTTPS 노출 (coalesced 측정용) — turnkey

**목적**: 평문 HTTP에선 `getCoalescedEvents`가 secure-context 전용이라 값이 안 잡힌다.
Caddy로 캡차(GPU `localhost:8000`) 앞에 HTTPS를 씌우면 `isSecureContext=true`가 되어 측정된다.
캡차 코드/설정은 **바꿀 필요 없음**(API 상대경로 · CORS `*` · site key 그대로).

## 당신이 준비할 것 (제 권한 밖)
1. **도메인** A레코드를 GPU 공개 IP `61.109.239.231` 로 지정 (예: `captcha.example.com`).
2. **카카오클라우드 보안그룹**에서 **80, 443** 개방 (ACME 인증 챌린지에 80 필요, 서비스에 443).
   - 소스는 팀원 IP로 제한 권장. 8000은 그대로 둬도 되고, HTTPS만 쓸 거면 닫아도 됨.
3. GPU에 **sudo(암호)** 로 아래 실행.

## GPU에서 실행 (root 필요)
> Ubuntu/Debian(apt) 가정. `cat /etc/os-release`로 확인. 다르면 알려주세요(바이너리 방식으로 드림).

```bash
# 1) Caddy 설치 (공식 저장소)
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy

# 2) 리버스 프록시 설정 (도메인만 본인 것으로)
sudo tee /etc/caddy/Caddyfile >/dev/null <<'EOF'
captcha.example.com {
    reverse_proxy localhost:8000
}
EOF

# 3) 적용 (Caddy가 Let's Encrypt 인증서 자동 발급·갱신)
sudo systemctl reload caddy   # 처음이면: sudo systemctl enable --now caddy
```

## 확인
- `https://captcha.example.com/?participant=이름-mouse` 로 접속 → 캡차 로드·풀이.
- 콘솔: `isSecureContext` → **true**, `typeof PointerEvent.prototype.getCoalescedEvents` → **"function"**.
- 풀이 후 DB에서 `coalesced_count` 값이 잡히는지(pointermove 건).

## 주의
- **80·443 둘 다** 열어야 함(80은 ACME용). DNS 전파 후 인증서 발급까지 수십 초~수 분.
- `reverse_proxy localhost:8000`은 GPU에서 Caddy를 돌릴 때. 210에서 돌리면 `reverse_proxy 10.0.1.52:8000`.
- 인증서 발급되면 `isSecureContext=true` → randomUUID·getCoalescedEvents·subtle 전부 정상(폴백 안 타도 됨).
- 실서비스도 어차피 HTTPS라 이 구성이 프로덕션에 그대로 근접.
