#!/usr/bin/env bash
# 활성 문항이 실제로 쓰는 자산만 뽑아 이미지 빌드용 tar 로 묶는다.
#
# 왜 필요한가 ────────────────────────────────────────────────────────────────
#   이 서비스는 문항 이미지·조각을 로컬 디스크(FINAL_DIR, 기본 data/final)에서 읽는다.
#   그런데 서버의 data/final 은 **3.5GB · 69,111개**다. 대부분은 비활성 문항과
#   라벨링 중간산물이라 서비스가 열지 않는다.
#
#   실측(2026-08-03):
#     문항 이미지 (captcha_questions.status='active')  151 MB ·  1,796개
#     조각        (captcha_objects.piece_path)         198 MB ·  6,442개
#     ────────────────────────────────────────────────────────────
#     합계                                             349 MB ·  8,238개
#
#   3.5GB 를 통째로 담으면 이미지가 못 쓸 크기가 된다. DB 가 가리키는 것만 담는다.
#
# 어디서 도나 ────────────────────────────────────────────────────────────────
#   **자산이 있는 서버에서** 돈다(옛 GPU 10.0.1.52 의 /srv/codex-workspaces/ms/swc).
#   DB 는 다른 서버(10.0.1.168)에 있으므로 네트워크로 붙는다.
#
# 쓰는 법 ────────────────────────────────────────────────────────────────────
#   sudo bash scripts/collect_active_assets.sh              # 기본 경로로
#   sudo SWC=/경로 OUT=/tmp/x.tar.gz bash scripts/collect_active_assets.sh
#
#   결과 tar 를 빌드 호스트로 옮겨 빌드 컨텍스트에서 풀면 data/final 이 채워진다.
#
# ★안전 ──────────────────────────────────────────────────────────────────────
#   읽기만 한다. 원본을 지우거나 옮기지 않는다.
#   DB 도 SELECT 만 한다.

set -euo pipefail

SWC="${SWC:-/srv/codex-workspaces/ms/swc}"
OUT="${OUT:-/tmp/captcha-active-assets.tar.gz}"
ENV_FILE="${ENV_FILE:-$SWC/.env}"

[ -d "$SWC/data/final" ] || { echo "자산 폴더가 없다: $SWC/data/final" >&2; exit 1; }
[ -f "$ENV_FILE" ] || { echo ".env 가 없다: $ENV_FILE" >&2; exit 1; }

# .env 에서 DB 접속 정보를 읽는다(값은 화면에 찍지 않는다)
DB_HOST=$(grep -E '^DB_HOST=' "$ENV_FILE" | tail -1 | cut -d= -f2-)
DB_PORT=$(grep -E '^DB_PORT=' "$ENV_FILE" | tail -1 | cut -d= -f2-)
DB_NAME=$(grep -E '^DB_NAME=' "$ENV_FILE" | tail -1 | cut -d= -f2-)
DB_USER=$(grep -E '^DB_USER=' "$ENV_FILE" | tail -1 | cut -d= -f2-)
DB_PASSWORD=$(grep -E '^DB_PASSWORD=' "$ENV_FILE" | tail -1 | cut -d= -f2-)
: "${DB_PORT:=3306}"

echo "══ 대상"
echo "   자산   $SWC/data/final"
echo "   DB     ${DB_USER}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
echo "   결과   $OUT"

# 비밀번호를 명령줄에 두면 ps 로 보인다 → 임시 설정파일로 넘기고 반드시 지운다
CNF=$(mktemp); trap 'rm -f "$CNF" "$LIST" 2>/dev/null' EXIT
chmod 600 "$CNF"
cat > "$CNF" <<CNFEOF
[client]
host=$DB_HOST
port=$DB_PORT
user=$DB_USER
password=$DB_PASSWORD
CNFEOF

LIST=$(mktemp)

echo
echo "══ DB 가 가리키는 파일 목록을 뽑는다 (SELECT 만)"
mysql --defaults-extra-file="$CNF" -N -B "$DB_NAME" <<'SQL' > "$LIST"
SELECT image_path FROM captcha_questions
 WHERE status='active' AND image_path IS NOT NULL AND image_path <> ''
UNION
SELECT o.piece_path FROM captcha_objects o
  JOIN captcha_questions q ON q.id = o.question_id
 WHERE q.status='active' AND o.piece_path IS NOT NULL AND o.piece_path <> '';
SQL

WANT=$(wc -l < "$LIST")
echo "   $WANT 개"

echo
echo "══ 실제로 있는지 확인한다 (없으면 빌드 후 404 가 된다)"
MISS=0
: > "${LIST}.ok"
while IFS= read -r p; do
  [ -z "$p" ] && continue
  if [ -f "$SWC/data/final/$p" ]; then
    printf 'data/final/%s\n' "$p" >> "${LIST}.ok"
  else
    MISS=$((MISS+1))
    [ "$MISS" -le 5 ] && echo "   ★없음: $p"
  fi
done < "$LIST"
HAVE=$(wc -l < "${LIST}.ok")
echo "   있음 $HAVE · 없음 $MISS"
if [ "$MISS" -gt 0 ]; then
  echo "   ★★없는 파일이 있다. 그 문항은 이미지가 안 뜬다. 확인하고 다시 돌릴 것." >&2
  exit 2
fi

echo
echo "══ 묶는다"
tar -czf "$OUT" -C "$SWC" -T "${LIST}.ok"
rm -f "${LIST}.ok"

echo "   $OUT  $(du -h "$OUT" | cut -f1)"
echo "   담긴 파일 $(tar -tzf "$OUT" | wc -l)개"
echo
echo "══ 다음"
echo "   빌드 호스트로 옮긴 뒤 빌드 컨텍스트에서:"
echo "     tar -xzf $(basename "$OUT")     # data/final/... 로 풀린다"
echo "     docker build -t catchap-captcha:<태그> ."
