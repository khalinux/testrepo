# ─────────────────────────────────────────────────────────────────────
# 이커머스 정산 시스템 · Dockerfile
# [AI 활용 CI/CD 교육] Day 2·3 실습 워크북 Part 6
#   (Day 1에서는 nginx + HTML 2줄 Dockerfile로 기초를 익혔습니다)
#
# 멀티스테이지 빌드 패턴
#   Stage 1 (builder) : 의존성 설치 · 빌드 도구 포함
#   Stage 2 (runtime) : 실행에 필요한 파일만 복사 → 이미지 경량화
#
# AI 활용 포인트:
#   "Python FastAPI 앱의 Dockerfile을 멀티스테이지 빌드로
#    200 MB 이하 · non-root · HEALTHCHECK 포함해서 작성해줘"
# ─────────────────────────────────────────────────────────────────────

# ── Stage 1 : Builder ─────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# 시스템 패키지 (빌드 시에만 필요)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
    && rm -rf /var/lib/apt/lists/*

# requirements.txt 먼저 복사 → 레이어 캐시 활용
# (소스만 변경되면 pip install 레이어는 캐시 재사용)
COPY requirements.txt .
RUN pip install --upgrade pip --quiet \
 && pip install --no-cache-dir --user -r requirements.txt


# ── Stage 2 : Runtime ────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

LABEL maintainer="cicd-education"
LABEL description="이커머스 정산 시스템"
LABEL version="1.0.0"

# 보안: root 가 아닌 전용 유저로 실행
RUN groupadd --system --gid 1001 appgroup \
 && useradd  --system --uid 1001 --gid appgroup --no-create-home appuser

WORKDIR /app

# Builder 에서 설치된 Python 패키지만 복사
COPY --from=builder /root/.local /home/appuser/.local

# 애플리케이션 소스
COPY --chown=appuser:appgroup src/ .

USER appuser

ENV PATH=/home/appuser/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

EXPOSE ${PORT}

# 쿠버네티스 liveness probe 와 연동
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c \
        "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" \
    || exit 1

# 주의 1) JSON 형식 CMD를 여러 줄로 나누려면 줄 끝에 \ 가 필요합니다 (없으면 빌드 오류)
# 주의 2) 이 앱은 데이터를 메모리에 저장하므로 워커는 1개로 둡니다.
#         워커가 2개 이상이면 프로세스마다 데이터가 따로 있어 주문 생성 → 완료 요청이 404가 될 수 있음
CMD ["uvicorn", "settlement.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1"]
