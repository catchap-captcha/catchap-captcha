#!/bin/sh
set -eu

SSH_KEY="${CAPTCHA_SSH_KEY:-/Users/apple/Desktop/캣챱/키페어/catchap_db_admin}"
# 새 서버 SSH 접속 대상(계정@호스트). 필요 시 CAPTCHA_SSH_HOST로 덮어쓸 수 있습니다.
SSH_HOST="${CAPTCHA_SSH_HOST:-db_admin@210.109.52.114}"
LOCAL_API_PORT="${CAPTCHA_TUNNEL_PORT:-18000}"

if [ ! -f "$SSH_KEY" ]; then
  echo "SSH 키를 찾을 수 없습니다: $SSH_KEY"
  echo "CAPTCHA_SSH_KEY 환경변수에 개인키 경로를 지정해주세요."
  exit 1
fi

ssh -N \
  -i "$SSH_KEY" \
  -o BatchMode=yes \
  -o ExitOnForwardFailure=yes \
  -o StrictHostKeyChecking=accept-new \
  -o ServerAliveInterval=30 \
  -L "${LOCAL_API_PORT}:127.0.0.1:8000" \
  "${SSH_HOST:-db_admin@210.109.52.114}" &
TUNNEL_PID=$!

cleanup() {
  kill "$TUNNEL_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

VITE_API_TARGET="http://127.0.0.1:${LOCAL_API_PORT}" npm run dev:ui
