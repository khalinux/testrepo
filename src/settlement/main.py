"""
이커머스 정산 시스템 - FastAPI 메인 애플리케이션
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[AI 활용 CI/CD 교육] Day 2·3 실습 워크북 Part 2~3

■ 역할
  이 파일은 FastAPI 웹 서버의 진입점(Entry Point)입니다.
  클라이언트(브라우저, curl, 앱)의 HTTP 요청을 받아서
  비즈니스 로직(SettlementService)으로 전달하고 결과를 반환합니다.

■ 실행 방법
  # 개발 모드 (코드 변경 시 자동 재시작)
  cd cicd-course/src
  uvicorn settlement.main:app --reload --host 0.0.0.0 --port 8000

■ API 확인
  Swagger UI : http://localhost:8000/docs      ← 브라우저에서 직접 테스트
  ReDoc       : http://localhost:8000/redoc    ← 읽기 좋은 문서 형태
  OpenAPI JSON: http://localhost:8000/openapi.json

■ FastAPI 동작 흐름
  클라이언트 요청
      ↓
  미들웨어 (CORS 처리)
      ↓
  라우터 함수 (@app.get, @app.post 등)
      ↓
  SettlementService (비즈니스 로직)
      ↓
  JSON 응답 반환
"""

# ── 표준 라이브러리 임포트 ────────────────────────────────────────────
import logging  # 로그 출력 (INFO, WARNING, ERROR 레벨 구분)
import uuid  # 고유한 주문 ID 생성용 (UUID v4)
from contextlib import asynccontextmanager  # 앱 시작/종료 이벤트 처리
from datetime import datetime, timedelta  # 날짜/시간 계산
from decimal import Decimal  # 금액 계산 (float 대신 사용 - 정밀도 문제 방지)
from typing import Annotated, List, Optional  # 타입 힌트 (Annotated: Python 3.9+)

# ── FastAPI 관련 임포트 ──────────────────────────────────────────────
from fastapi import FastAPI, HTTPException, Query

# FastAPI   : 웹 프레임워크 본체
# HTTPException : HTTP 에러 응답 (404, 500 등)
# Query     : URL 쿼리 파라미터 정의 (?merchant_id=M-001 형태)
from fastapi.middleware.cors import CORSMiddleware

# CORS(Cross-Origin Resource Sharing): 다른 도메인에서의 API 호출 허용 설정
# 예: 프론트엔드(localhost:3000)에서 백엔드(localhost:8000) 호출 시 필요
# ── 내부 모듈 임포트 ─────────────────────────────────────────────────
from settlement.models.models import (
    HealthResponse,  # 헬스체크 응답 모델
    Order,  # 주문 데이터 모델
    SettlementRecord,  # 정산 레코드 모델
    SettlementRequest,  # 정산 생성 요청 모델
    SettlementStatus,  # 정산 상태 Enum (PENDING, PROCESSING, COMPLETED 등)
)
from settlement.services.settlement_service import SettlementService

# 실제 비즈니스 로직을 담당하는 서비스 클래스


# ── 로깅 설정 ────────────────────────────────────────────────────────
# basicConfig: 로그 출력 형식과 레벨을 전역으로 설정
# format: 시간 | 레벨 | 모듈명 - 메시지 형태로 출력
# 예: 2026-06-30 10:00:00,000 INFO settlement.main - 정산 시스템 기동 완료
logging.basicConfig(
    level=logging.INFO,  # INFO 이상 레벨만 출력 (DEBUG는 제외)
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)
# __name__ = "settlement.main" → 어떤 모듈에서 출력된 로그인지 구분 가능


# ── 전역 서비스 인스턴스 ─────────────────────────────────────────────
# 애플리케이션 전체에서 하나의 SettlementService 인스턴스를 공유합니다.
#
# ※ 교육용 설계입니다.
#   실무에서는 DI(의존성 주입) 컨테이너나 DB 세션을 사용합니다.
#   예: def get_service() -> SettlementService: ...
#       @app.post("/orders")
#       async def create(svc: SettlementService = Depends(get_service)):
svc = SettlementService()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 앱 생명주기 관리 (Lifespan)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI 앱의 시작과 종료 시점에 실행할 코드를 정의합니다.

    @asynccontextmanager 패턴:
      yield 이전 → 앱 시작 시 실행 (DB 연결, 초기 데이터 로드 등)
      yield 이후 → 앱 종료 시 실행 (DB 연결 해제, 리소스 정리 등)

    실무 활용 예:
      async with lifespan(app):
          # DB 커넥션 풀 초기화
          await database.connect()
          yield
          # 종료 시 연결 해제
          await database.disconnect()
    """
    # ── 앱 시작 시 ────────────────────────────────────────────────────
    _seed_sample_data()  # 실습용 샘플 데이터 생성
    logger.info("정산 시스템 기동 완료")

    yield  # ← 여기서 실제 서버가 요청을 받기 시작합니다

    # ── 앱 종료 시 ────────────────────────────────────────────────────
    logger.info("정산 시스템 종료")


def _seed_sample_data():
    """
    실습용 샘플 데이터를 메모리에 생성합니다.

    생성 내용:
      - 판매자 M-001, M-002 각각 5건의 주문
      - 모든 주문은 COMPLETED 상태 (정산 계산 대상)
      - 주문 완료 시각을 0~4일 전으로 분산 (기간 필터 테스트용)

    금액 계산 예시 (M-001):
      i=0: 10,000 × 1 × 1 = 10,000원  (0일 전 완료)
      i=1: 10,000 × 2 × 1 = 20,000원  (1일 전 완료)
      i=2: 10,000 × 3 × 1 = 30,000원  (2일 전 완료)
      ...

    ※ 실무에서는 이 함수 대신 DB 마이그레이션(alembic 등)을 사용합니다.
    """
    for m_idx, merchant in enumerate(["M-001", "M-002"]):
        for i in range(5):
            # uuid4().hex[:6] → 6자리 랜덤 16진수 문자열 (예: "A3F9B2")
            # 매 실행마다 다른 주문 ID 생성
            order = Order(
                order_id=f"ORD-{uuid.uuid4().hex[:6].upper()}",
                merchant_id=merchant,
                customer_id=f"C-{i+1:03d}",  # C-001, C-002, ... C-005
                # m_idx+1: 판매자별로 금액 배율 다르게 (M-001=1배, M-002=2배)
                amount=Decimal(str(10_000 * (i + 1) * (m_idx + 1))),
            )
            svc.add_order(order)

            done = svc.complete_order(order.order_id)
            if done:
                # timedelta(days=i): 최근 주문일수록 최근 날짜로 설정
                # i=0 → 오늘, i=1 → 1일 전, i=4 → 4일 전
                done.completed_at = datetime.utcnow() - timedelta(days=i)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FastAPI 앱 인스턴스 생성
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
app = FastAPI(
    title="이커머스 정산 시스템",
    description="""
## CI/CD 교육용 이커머스 정산 REST API

### 주요 기능
- 📦 **주문 관리**: 주문 생성·완료·목록 조회
- 💰 **정산 계산**: 판매자 기간별 매출·수수료·순정산액 계산
- 🔄 **정산 처리**: 정산 상태 관리 (PENDING → COMPLETED)

### CI/CD 실습 포인트
이 API는 GitHub Actions 파이프라인에서 자동으로 테스트되고
Docker 이미지로 빌드되어 kind 클러스터에 자동 배포됩니다.
""",
    version="1.0.0",
    lifespan=lifespan,  # 위에서 정의한 생명주기 함수 연결
)


# ── CORS 미들웨어 설정 ────────────────────────────────────────────────
# CORS: 웹 브라우저의 보안 정책
# 기본적으로 브라우저는 다른 출처(origin)의 API 호출을 차단합니다.
# 예: 프론트엔드 localhost:3000 → 백엔드 localhost:8000 호출 시 차단
#
# allow_origins=["*"]: 모든 출처 허용 (교육용)
# 실무에서는 ["https://yourdomain.com"] 처럼 특정 도메인만 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 모든 도메인 허용
    allow_methods=["*"],  # 모든 HTTP 메서드 허용 (GET, POST, PUT 등)
    allow_headers=["*"],  # 모든 헤더 허용
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 시스템 엔드포인트 (쿠버네티스 연동)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["시스템"],
    summary="헬스체크",
)
async def health():
    """
    애플리케이션 상태를 확인하는 헬스체크 엔드포인트입니다.

    ■ 쿠버네티스 연동
      Liveness Probe: Pod가 살아있는지 주기적으로 확인
        → 이 엔드포인트가 실패하면 쿠버네티스가 Pod를 재시작합니다.

      deployment.yaml 설정:
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 30   # 30초 후 첫 검사
          periodSeconds: 15         # 15초마다 검사

    ■ 응답 예시
      {"status": "ok", "version": "1.0.0", "timestamp": "2026-06-30T..."}

    ■ curl 테스트
      curl localhost:8000/health
    """
    return HealthResponse(status="ok")


@app.get(
    "/ready",
    tags=["시스템"],
    summary="준비 상태 확인",
)
async def ready():
    """
    서비스가 트래픽을 받을 준비가 됐는지 확인하는 엔드포인트입니다.

    ■ Liveness vs Readiness 차이
      /health (Liveness):  "프로세스가 살아있나?" → 실패 시 Pod 재시작
      /ready  (Readiness): "요청 처리 가능한가?" → 실패 시 트래픽 차단

    ■ 실무 활용 예
      실무에서는 여기서 DB 연결, 캐시 서버 연결 등을 검사합니다:
        await database.execute("SELECT 1")  # DB 핑
        await redis.ping()                   # Redis 핑

    ■ 교육용 구현
      이 코드에서는 항상 ready를 반환합니다.
    """
    # 실무 예시:
    # try:
    #     await db.execute("SELECT 1")     # DB 연결 확인
    #     await cache.ping()               # 캐시 서버 확인
    # except Exception:
    #     raise HTTPException(503, "서비스 준비 안 됨")
    return {"status": "ready"}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 주문 API
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@app.post(
    "/api/v1/orders",
    response_model=Order,  # 반환 타입: Order 모델 (Swagger에 자동 표시)
    status_code=201,  # 201 Created: 리소스 생성 성공 시 표준 코드
    tags=["주문"],
    summary="주문 생성",
)
async def create_order(order: Order):
    """
    새로운 주문을 생성합니다.

    ■ 요청 본문 (Request Body)
      FastAPI가 요청의 JSON을 자동으로 Order 모델로 변환합니다.
      Pydantic이 유효성 검사를 수행하며, 실패 시 422 오류를 반환합니다.

    ■ curl 테스트 예시
      curl -X POST localhost:8000/api/v1/orders \\
        -H "Content-Type: application/json" \\
        -d '{
          "order_id": "ORD-TEST-001",
          "merchant_id": "M-001",
          "customer_id": "C-001",
          "amount": "50000",
          "status": "pending",
          "created_at": "2026-06-30T00:00:00"
        }'

    ■ HTTP 상태 코드
      201: 주문 생성 성공
      422: 요청 데이터 유효성 오류 (금액이 음수 등)
    """
    return svc.add_order(order)


@app.put(
    "/api/v1/orders/{order_id}/complete",
    response_model=Order,
    tags=["주문"],
    summary="주문 완료 처리",
)
async def complete_order(order_id: str):
    """
    주문 상태를 PENDING → COMPLETED로 변경합니다.

    ■ 경로 파라미터 (Path Parameter)
      {order_id}: URL 경로의 일부로 전달되는 값
      예: PUT /api/v1/orders/ORD-ABC123/complete
          → order_id = "ORD-ABC123"

    ■ 완료 처리가 중요한 이유
      정산 계산은 COMPLETED 상태의 주문만 대상으로 합니다.
      PENDING 상태 주문은 정산에서 제외됩니다.
      (결제 취소·반품 가능성이 있기 때문)

    ■ curl 테스트 예시
      curl -X PUT localhost:8000/api/v1/orders/ORD-ABC123/complete

    ■ HTTP 상태 코드
      200: 완료 처리 성공
      404: 해당 order_id의 주문 없음
    """
    result = svc.complete_order(order_id)
    if not result:
        # HTTPException: FastAPI의 표준 에러 응답
        # 클라이언트에게 {"detail": "주문을 찾을 수 없습니다: ORD-XXX"} 형태로 전달
        raise HTTPException(
            status_code=404,
            detail=f"주문을 찾을 수 없습니다: {order_id}",
        )
    return result


@app.get(
    "/api/v1/orders",
    response_model=List[Order],  # 반환 타입: Order 목록
    tags=["주문"],
    summary="주문 목록 조회",
)
async def list_orders(
    merchant_id: Annotated[
        Optional[str],
        Query(
            description="판매자 ID 필터",  # Swagger에 표시될 설명
            example="M-001",  # Swagger 예시 값
        ),
    ] = None,  # 기본값 None → 파라미터 없으면 전체 조회
):
    """
    주문 목록을 조회합니다. 판매자 ID로 필터링 가능합니다.

    ■ 쿼리 파라미터 (Query Parameter)
      URL 뒤에 ?key=value 형태로 전달
      예: GET /api/v1/orders?merchant_id=M-001

    ■ curl 테스트 예시
      # 전체 주문 조회
      curl localhost:8000/api/v1/orders

      # 특정 판매자의 주문만 조회
      curl "localhost:8000/api/v1/orders?merchant_id=M-001"
    """
    return svc.get_orders(merchant_id)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 정산 API
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@app.post(
    "/api/v1/settlements",
    response_model=SettlementRecord,
    status_code=201,
    tags=["정산"],
    summary="정산 생성 (기간별 계산)",
)
async def create_settlement(req: SettlementRequest):
    """
    특정 판매자의 기간별 정산을 계산하고 레코드를 생성합니다.

    ■ 정산 계산 공식
      총 매출액 = 해당 기간 COMPLETED 주문의 amount 합계
      총 수수료 = 총 매출액 × fee_rate (기본 3%)
      순 정산액 = 총 매출액 - 총 수수료

    ■ 요청 본문 예시
      {
        "merchant_id": "M-001",
        "period_start": "2026-06-01T00:00:00",
        "period_end":   "2026-06-30T23:59:59"
      }

    ■ curl 테스트 예시
      curl -X POST localhost:8000/api/v1/settlements \\
        -H "Content-Type: application/json" \\
        -d '{
          "merchant_id": "M-001",
          "period_start": "2026-06-01T00:00:00",
          "period_end": "2026-06-30T23:59:59"
        }'

    ■ HTTP 상태 코드
      201: 정산 레코드 생성 성공
      500: 정산 계산 중 오류 발생
    """
    try:
        return svc.calculate_settlement(
            req.merchant_id,
            req.period_start,
            req.period_end,
        )
    except Exception as e:
        # 예상치 못한 오류 발생 시 500 Internal Server Error 반환
        # 실무에서는 더 구체적인 에러 타입을 정의해서 처리합니다.
        logger.error("정산 생성 오류: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get(
    "/api/v1/settlements",
    response_model=List[SettlementRecord],
    tags=["정산"],
    summary="정산 목록 조회",
)
async def list_settlements(
    merchant_id: Annotated[
        Optional[str],
        Query(
            description="판매자 ID 필터",
            example="M-001",
        ),
    ] = None,
    status: Annotated[
        Optional[SettlementStatus],
        Query(
            description="정산 상태 필터 (pending / processing / completed / failed)",
            example="pending",
        ),
    ] = None,
):
    """
    정산 레코드 목록을 조회합니다.
    판매자 ID와 정산 상태로 필터링할 수 있습니다.

    ■ curl 테스트 예시
      # 전체 정산 목록
      curl localhost:8000/api/v1/settlements

      # M-001 판매자의 완료된 정산만
      curl "localhost:8000/api/v1/settlements?merchant_id=M-001&status=completed"
    """
    return svc.list_settlements(merchant_id, status)


@app.post(
    "/api/v1/settlements/{settlement_id}/process",
    response_model=SettlementRecord,
    tags=["정산"],
    summary="정산 처리 실행",
)
async def process_settlement(settlement_id: str):
    """
    PENDING 상태의 정산을 실제로 처리합니다.
    (실무에서는 이 단계에서 은행 이체 API를 호출합니다.)

    ■ 정산 처리 흐름
      PENDING → PROCESSING → COMPLETED (성공)
                           → FAILED    (실패)

    ■ curl 테스트 예시
      # 먼저 정산 목록에서 settlement_id 확인
      curl localhost:8000/api/v1/settlements

      # 정산 처리 실행
      curl -X POST localhost:8000/api/v1/settlements/STL-XXXXXXXX/process

    ■ HTTP 상태 코드
      200: 정산 처리 완료
      404: 해당 정산 레코드 없음
    """
    result = svc.process_settlement(settlement_id)
    if not result:
        raise HTTPException(
            status_code=404,
            detail=f"정산 레코드를 찾을 수 없습니다: {settlement_id}",
        )
    return result
