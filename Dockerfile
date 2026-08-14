# CatChap 메인 캡차 (드래그 캡차) — 프로덕션 이미지
#
# 왜 이 파일이 필요한가 — 지금 이 서비스는 옛 GPU 서버에서 `ms` 계정이 손으로 띄운
# uvicorn 프로세스로 돌고 있다. systemd 유닛도 없어 **서버를 재부팅하면 조용히 안 올라온다.**
# 컨테이너로 만들어 쿠버네티스에 올리면 그 문제가 사라지고 파드를 2벌로 늘릴 수 있다.
#
# ★단일 단계다. 화면 빌드 결과(static/dist)가 **이미 git에 커밋돼 있어**
#   node 빌드 단계가 필요 없다(2026-08-03 확인 — 서버의 static/dist 4개 파일과
#   git의 것이 해시까지 완전히 같다). 화면을 고치면 `npm run build` 후 커밋한다.

FROM python:3.12-slim

# ★어떤 커밋으로 만든 이미지인지 심는다. 없으면 "unknown" 이다.
#   빌드할 때만 넣을 수 있는 값이라 여기 말고는 넣을 곳이 없다.
ARG GIT_SHA=unknown
ENV GIT_SHA=$GIT_SHA
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Seoul

WORKDIR /app

# 시간대를 한국으로 고정 — 이 서비스는 챌린지 만료·레이트리밋을 로컬 시각으로 잡는다.
# 컨테이너 기본(UTC)이면 9시간 어긋나 만료가 이르게 찍힌다. (백엔드 이미지와 같은 처리)
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && ln -sf /usr/share/zoneinfo/Asia/Seoul /etc/localtime \
    && echo "Asia/Seoul" > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# ★런타임 의존성만 설치한다 — requirements.txt를 그대로 쓰지 않는 이유:
#
#   requirements.txt 에는 ultralytics(YOLO)·opencv·numpy·pandas 가 있는데
#   **서비스(app/)는 이 넷을 하나도 import 하지 않는다**(2026-08-03 전수 확인).
#   전부 scripts/ 의 데이터셋 생성·라벨링 도구가 쓰는 것이다.
#   그대로 담으면 5.6GB, 빼면 수백 MB다.
#
#   ★scripts/ 를 돌려야 할 때는 이 이미지가 아니라 개발 환경에서 돌린다
#     (문항 생성·라벨링은 배포와 무관한 작업이다).
COPY requirements.runtime.txt ./
RUN pip install --no-cache-dir -r requirements.runtime.txt

# 앱 코드 + 화면(빌드 결과 포함)
COPY app ./app
COPY static ./static

# ★캡차 문항 자산 (이미지·조각) — 2026-08-07 부터 ★이미지에 굽지 않는다
#
#   예전에는 여기서 `COPY data/final ./data/final` 로 367MB(8,238개)를 구웠다.
#   그 자산은 .gitignore 로 막혀 있어 **git 에 없기 때문에**, GitHub Actions 가
#   이미지를 구우면 "그런 파일 없음"으로 실패했다. 손으로만 구울 수 있는 이미지였다.
#
#   ★이제 app/asset_storage.py 가 오브젝트 스토리지에서 읽는다
#     (백엔드 app/services/media_storage.py 와 같은 방식).
#
#     운영   ASSET_STORAGE_BACKEND=object  + ASSET_BUCKET · ASSET_S3_* 설정
#     개발   ASSET_STORAGE_BACKEND=local   (기본값) → data/final 을 그대로 읽는다.
#            로컬에서 자산이 필요하면 scripts/collect_active_assets.sh 로 뽑아 둔다.
#
#   ★얻는 것 — 이미지가 367MB 작아지고, 자산이 바뀌어도 ★재빌드가 필요 없다.

EXPOSE 8000

# 컨테이너 자체 헬스체크 — 쿠버네티스 probe 와 별개로 도커 단독 실행에서도 상태를 본다.
# ★/health/ready 가 아니라 /health/live 를 쓴다: ready 는 DB·행동AI 까지 확인하므로
#   그쪽이 잠깐 흔들리면 컨테이너를 죽여야 할 이유가 없는데도 unhealthy 가 된다.
#   (쿠버네티스에서는 readinessProbe=/health/ready · livenessProbe=/health/live 로 나눈다)
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live',timeout=3).status==200 else 1)"

# 워커 2개 — 옛 systemd 유닛(deploy/drag-captcha.service)과 같은 설정.
# 쿠버네티스에서 파드를 2벌 띄우므로 총 4워커가 된다.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
