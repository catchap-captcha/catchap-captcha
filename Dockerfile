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

# ★캡차 문항 자산 (이미지·조각)
#
#   이 서비스는 자산을 로컬 디스크에서 읽는다(FINAL_DIR, 기본 data/final).
#   서버의 data/final 은 3.5GB(69,111개)지만 **실제로 쓰이는 건 349MB(8,238개)** 다.
#   나머지는 비활성 문항·원본·라벨링 중간산물이다.
#
#   빌드 전에 scripts/collect_active_assets.sh 로 활성 문항 자산만 뽑아
#   빌드 컨텍스트의 data/final 에 둬야 한다. 없으면 빌드는 되지만 문항 이미지가 404가 된다.
#
#   ★★나중에 Object Storage 에서 읽도록 바꿀 예정이다(백엔드 media_storage.py 와 같은 방식).
#     그때 이 COPY 는 빠지고 이미지가 다시 작아진다. 지금 굽는 이유는
#     **이전 중에 동작까지 바꾸면 문제가 생겼을 때 원인을 못 가리기** 때문이다.
COPY data/final ./data/final

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
