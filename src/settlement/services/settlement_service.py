"""
이커머스 정산 시스템 - 비즈니스 로직 서비스
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[AI 활용 CI/CD 교육] Day 2·3 실습 워크북 Part 2

■ 역할
  이 파일은 정산 시스템의 핵심 비즈니스 로직을 담당합니다.
  main.py(API 레이어)에서 호출되며, 데이터 처리와 계산을 수행합니다.

■ 설계 원칙
  1. 단일 책임 원칙(SRP): 정산 계산만 담당 (HTTP 처리는 main.py가)
  2. 의존성 역전: 저장소(Repository)를 직접 구현하지 않고 내부에 보유
  3. 인메모리 저장소: 교육용 단순화 (실무에서는 PostgreSQL 등 DB 사용)

■ 실무와의 차이
  교육용 (현재)          실무 환경
  ────────────────────   ──────────────────────────
  인메모리 리스트        PostgreSQL 데이터베이스
  단일 인스턴스          다중 Pod (쿠버네티스)
  동기 처리              비동기 처리 (asyncio)
  파일 내 저장           영속적 저장 (DB)

■ AI 활용 포인트
  이 파일 전체를 Claude.ai에 붙여넣고
  "이 코드를 PostgreSQL + SQLAlchemy 비동기 버전으로 변환해줘"
  라고 요청해보세요.
"""

# ── 표준 라이브러리 임포트 ────────────────────────────────────────────
import logging  # 로그 출력
import uuid  # 정산 ID 생성용 고유값
from datetime import datetime  # 처리 시각 기록
from decimal import Decimal  # 금액 정밀 계산 (float 사용 시 0.1+0.2≠0.3 문제 발생)
from typing import List, Optional  # 타입 힌트

# ── 내부 모듈 임포트 ─────────────────────────────────────────────────
from settlement.models.models import (
    Order,  # 주문 데이터 클래스
    OrderStatus,  # 주문 상태 Enum: PENDING, COMPLETED, CANCELLED, REFUNDED
    SettlementRecord,  # 정산 레코드 데이터 클래스
    SettlementStatus,  # 정산 상태 Enum: PENDING, PROCESSING, COMPLETED, FAILED
)

# 이 모듈 전용 로거 생성
# getLogger(__name__) → "settlement.services.settlement_service" 로 식별
logger = logging.getLogger(__name__)


class SettlementService:
    """
    정산 비즈니스 로직 서비스 클래스

    ■ 주요 메서드
      add_order()            : 주문 추가
      complete_order()       : 주문 완료 처리
      get_orders()           : 주문 목록 조회
      calculate_settlement() : 기간별 정산 계산  ← 핵심 메서드
      process_settlement()   : 정산 처리 실행
      list_settlements()     : 정산 목록 조회

    ■ 내부 저장소 구조
      _orders      : List[Order]            주문 데이터 목록
      _settlements : List[SettlementRecord] 정산 레코드 목록

    ■ 인스턴스 생명주기
      main.py에서 앱 시작 시 1회 생성 → 앱 종료까지 유지
      (서버 재시작 시 데이터 초기화됨 — 교육용 특성)
    """

    def __init__(self):
        """
        서비스 초기화: 인메모리 저장소 생성

        실무 버전에서는 여기에 DB 세션이나 레포지토리 객체를 주입받습니다:
          def __init__(self, db_session: AsyncSession):
              self._db = db_session
        """
        # 주문 데이터 저장 리스트
        # 타입 힌트 List[Order]: Order 객체만 담을 수 있음을 명시
        self._orders: List[Order] = []

        # 정산 레코드 저장 리스트
        self._settlements: List[SettlementRecord] = []

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 주문 관련 메서드
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def add_order(self, order: Order) -> Order:
        """
        새로운 주문을 저장소에 추가합니다.

        Args:
            order: 추가할 주문 객체 (main.py에서 HTTP 요청 본문으로 전달됨)

        Returns:
            Order: 추가된 주문 객체 (동일 객체 반환 — API 응답용)

        동작 흐름:
          1. _orders 리스트에 order 추가
          2. 로그 기록
          3. 추가된 order 반환
        """
        self._orders.append(order)

        # 로그: 파이프라인 형태로 key=value 기록 (검색 용이)
        # 실제 출력: "주문 추가 | id=ORD-ABC123 amount=50000"
        logger.info("주문 추가 | id=%s amount=%s", order.order_id, order.amount)

        return order

    def complete_order(self, order_id: str) -> Optional[Order]:
        """
        주문 상태를 PENDING → COMPLETED로 변경합니다.

        Args:
            order_id: 완료 처리할 주문의 고유 ID

        Returns:
            Order: 완료 처리된 주문 객체
            None : order_id에 해당하는 주문이 없을 경우

        ■ 완료 처리 시 변경 사항
          1. status: PENDING → COMPLETED
          2. completed_at: None → 현재 시각 (UTC 기준)

        ■ completed_at이 중요한 이유
          정산 계산 시 period_start ≤ completed_at ≤ period_end 조건으로
          해당 기간의 완료 주문만 필터링합니다.
          completed_at이 None이면 정산 대상에서 제외됩니다.

        ■ Optional[Order] 반환 타입
          Optional[Order] = Union[Order, None]
          → Order를 반환하거나 None을 반환할 수 있음
          → 호출하는 쪽에서 None 체크 필요 (main.py에서 404 처리)
        """
        # 선형 탐색: order_id가 일치하는 주문 찾기
        # 실무에서는 DB의 WHERE order_id = ? 쿼리로 처리
        for order in self._orders:
            if order.order_id == order_id:
                # 상태 변경
                order.status = OrderStatus.COMPLETED

                # 완료 시각 기록 (UTC 사용 — 서버 표준시간)
                # 실무 주의: datetime.now()는 로컬 시간대, utcnow()는 UTC
                # Python 3.11+: datetime.now(timezone.utc) 권장
                order.completed_at = datetime.utcnow()

                logger.info("주문 완료 | id=%s", order_id)
                return order

        # 주문을 찾지 못한 경우
        logger.warning("주문 없음 | id=%s", order_id)
        return None  # main.py에서 이 None을 받아 404 응답으로 처리

    def get_orders(self, merchant_id: Optional[str] = None) -> List[Order]:
        """
        주문 목록을 조회합니다.

        Args:
            merchant_id: 판매자 ID (None이면 전체 조회)

        Returns:
            List[Order]: 조건에 맞는 주문 목록

        ■ 리스트 컴프리헨션 활용
          [o for o in self._orders if o.merchant_id == merchant_id]
          → self._orders에서 merchant_id가 일치하는 주문만 필터링

        ■ list(self._orders) 복사본 반환 이유
          원본 리스트를 직접 반환하면 외부에서 수정 가능
          복사본 반환으로 데이터 안정성 보장
        """
        if merchant_id:
            # 특정 판매자의 주문만 필터링
            return [o for o in self._orders if o.merchant_id == merchant_id]

        # merchant_id가 없으면 전체 반환 (복사본)
        return list(self._orders)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 정산 관련 메서드
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def calculate_settlement(
        self,
        merchant_id: str,
        period_start: datetime,
        period_end: datetime,
    ) -> SettlementRecord:
        """
        특정 판매자의 기간별 정산을 계산하고 레코드를 생성합니다.

        ■ 이 메서드가 핵심입니다 — 정산 계산 로직

        Args:
            merchant_id  : 정산할 판매자 ID (예: "M-001")
            period_start : 정산 기간 시작일시 (예: 2026-06-01 00:00:00)
            period_end   : 정산 기간 종료일시 (예: 2026-06-30 23:59:59)

        Returns:
            SettlementRecord: 계산된 정산 레코드

        ■ 정산 대상 조건 (4가지 모두 충족해야 함)
          ① merchant_id 일치
          ② status == COMPLETED  (완료된 주문만)
          ③ completed_at is not None  (완료 시각이 있는 것만)
          ④ period_start ≤ completed_at ≤ period_end  (기간 내)

        ■ 정산 계산 공식
          총 매출액 = 조건 충족 주문의 amount 합계
          총 수수료 = 각 주문의 fee_amount 합계
                    (fee_amount = amount × fee_rate, 기본 3%)
          순 정산액 = 총 매출액 - 총 수수료

        ■ 계산 예시
          주문 1: 100,000원  수수료 3% = 3,000원
          주문 2:  50,000원  수수료 3% = 1,500원
          ─────────────────────────────────────
          총 매출액: 150,000원
          총 수수료:   4,500원
          순 정산액: 145,500원

        ■ Decimal 사용 이유
          파이썬의 float는 이진수 부동소수점이라 계산 오류 발생:
            0.1 + 0.2 = 0.30000000000000004  (오류!)
          Decimal은 10진수 정밀 계산:
            Decimal("0.1") + Decimal("0.2") = Decimal("0.3")  (정확!)
          금액 계산에는 반드시 Decimal 사용
        """
        logger.info(
            "정산 계산 시작 | merchant=%s, %s ~ %s",
            merchant_id,
            period_start,
            period_end,
        )

        # ── 정산 대상 주문 필터링 ─────────────────────────────────────
        # 리스트 컴프리헨션으로 4가지 조건을 동시에 검사
        target = [
            o
            for o in self._orders
            if (
                o.merchant_id == merchant_id  # ① 판매자 일치
                and o.status == OrderStatus.COMPLETED  # ② 완료 상태
                and o.completed_at is not None  # ③ 완료 시각 존재
                and period_start <= o.completed_at <= period_end  # ④ 기간 내
            )
        ]
        # target은 정산 대상 주문 목록 (List[Order])

        # ── 금액 계산 ─────────────────────────────────────────────────
        # sum()의 두 번째 인수 Decimal("0"): 초기값 (빈 목록일 때 0 반환)
        # o.amount    : 주문 금액 (models.py의 Order.amount)
        # o.fee_amount: 수수료 (models.py의 @property로 자동 계산)
        total_sales = sum((o.amount for o in target), Decimal("0"))
        total_fee = sum((o.fee_amount for o in target), Decimal("0"))

        # 순 정산액: 판매자가 실제로 받는 금액
        net_amount = total_sales - total_fee

        # ── 정산 레코드 생성 ──────────────────────────────────────────
        # uuid4().hex[:8]: 8자리 16진수 랜덤 문자열
        # upper(): 대문자로 변환 (가독성)
        # 예: STL-A3F9B2C1
        record = SettlementRecord(
            settlement_id=f"STL-{uuid.uuid4().hex[:8].upper()}",
            merchant_id=merchant_id,
            period_start=period_start,
            period_end=period_end,
            total_sales=total_sales,
            total_fee=total_fee,
            net_amount=net_amount,
            order_count=len(target),  # 정산 대상 주문 건수
            status=SettlementStatus.PENDING,  # 초기 상태: 처리 대기
        )

        # 저장소에 레코드 추가
        self._settlements.append(record)

        logger.info(
            "정산 레코드 생성 | id=%s 건수=%d 매출=%s 수수료=%s 정산액=%s",
            record.settlement_id,
            len(target),
            total_sales,
            total_fee,
            net_amount,
        )

        return record

    def process_settlement(self, settlement_id: str) -> Optional[SettlementRecord]:
        """
        정산 레코드를 실제로 처리합니다. (PENDING → COMPLETED)

        Args:
            settlement_id: 처리할 정산 레코드 ID (예: "STL-A3F9B2C1")

        Returns:
            SettlementRecord: 처리 결과가 반영된 정산 레코드
            None            : 해당 ID의 레코드가 없을 경우

        ■ 상태 전이 흐름
          PENDING
            ↓ (process_settlement 호출)
          PROCESSING  ← 처리 시작 (은행 API 호출 중)
            ↓ 성공
          COMPLETED   ← processed_at 기록
            ↓ 실패
          FAILED      ← error_message 기록

        ■ 실무에서 PROCESSING 상태가 필요한 이유
          은행 이체 API 호출은 수 초~수십 초 걸릴 수 있습니다.
          처리 중 상태를 PROCESSING으로 표시해야:
          1. 중복 처리 방지 (PENDING인 것만 처리)
          2. 현재 상태 추적 가능
          3. 장애 시 어디서 멈췄는지 파악 가능

        ■ 실무 연동 코드 예시
          # PG사 또는 은행 API 호출
          transfer_result = await bank_api.transfer(
              from_account="platform_account",
              to_account=merchant.bank_account,
              amount=record.net_amount,
              reference=record.settlement_id,
          )
        """
        # 정산 레코드 조회 (없으면 None 반환 → main.py에서 404 처리)
        record = self._find_settlement(settlement_id)
        if not record:
            return None

        try:
            # ── 처리 시작: PENDING → PROCESSING ─────────────────────
            record.status = SettlementStatus.PROCESSING
            logger.info("정산 처리 시작 | id=%s 금액=%s", settlement_id, record.net_amount)

            # ── 실제 이체 처리 (교육용: 생략) ────────────────────────
            # 실무 코드:
            # await bank_api.transfer(
            #     account=merchant.bank_account,
            #     amount=record.net_amount,
            #     reference=record.settlement_id,
            # )

            # ── 처리 완료: PROCESSING → COMPLETED ───────────────────
            record.status = SettlementStatus.COMPLETED
            record.processed_at = datetime.utcnow()  # 완료 시각 기록
            logger.info("정산 처리 완료 | id=%s", settlement_id)

        except Exception as exc:
            # ── 처리 실패: → FAILED ───────────────────────────────────
            # 어떤 예외가 발생해도 FAILED로 상태 변경
            # error_message에 오류 내용 저장 (나중에 재처리나 원인 분석에 활용)
            record.status = SettlementStatus.FAILED
            record.error_message = str(exc)
            logger.error("정산 처리 실패 | id=%s 오류=%s", settlement_id, exc)
            # 예외를 다시 raise하지 않음 → 호출자에게 FAILED 레코드 반환

        return record

    def list_settlements(
        self,
        merchant_id: Optional[str] = None,
        status: Optional[SettlementStatus] = None,
    ) -> List[SettlementRecord]:
        """
        정산 레코드 목록을 조회합니다. 복수 조건으로 필터링 가능합니다.

        Args:
            merchant_id: 판매자 ID 필터 (None이면 전체)
            status     : 정산 상태 필터 (None이면 전체)

        Returns:
            List[SettlementRecord]: 조건에 맞는 정산 레코드 목록

        ■ 다중 필터 동작 방식
          result = 전체 목록
          → merchant_id 필터 적용
          → status 필터 적용
          → 결과 반환

          예: merchant_id="M-001", status=COMPLETED
          → M-001의 완료된 정산만 반환

        ■ 중간 변수 result 사용 이유
          원본 self._settlements를 수정하지 않으면서
          단계적으로 필터를 적용하기 위해
        """
        # 전체 목록에서 시작 (참조 복사 — 아래에서 재할당됨)
        result = self._settlements

        # merchant_id 필터: 특정 판매자의 정산만
        if merchant_id:
            result = [s for s in result if s.merchant_id == merchant_id]

        # status 필터: 특정 상태의 정산만
        # 예: status=SettlementStatus.PENDING → 처리 대기 중인 것만
        if status:
            result = [s for s in result if s.status == status]

        return result

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 내부(Private) 메서드
    # 이름 앞에 _ 붙임 → 클래스 외부에서 직접 호출하지 않는 규칙
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _find_settlement(self, settlement_id: str) -> Optional[SettlementRecord]:
        """
        settlement_id로 정산 레코드를 조회합니다. (내부 전용)

        Args:
            settlement_id: 찾을 정산 레코드 ID

        Returns:
            SettlementRecord: 찾은 레코드
            None            : 없으면 None

        ■ 왜 별도 메서드로 분리했나?
          process_settlement()에서 사용하는 레코드 조회 로직을
          재사용 가능한 단위로 추출했습니다.
          (DRY 원칙: Don't Repeat Yourself)

        ■ 실무에서는
          DB 쿼리로 대체됩니다:
          return await db.get(SettlementRecord, settlement_id)
        """
        for s in self._settlements:
            if s.settlement_id == settlement_id:
                return s
        return None
