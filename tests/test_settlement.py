"""
SettlementService 단위 테스트 (Mock 없이 실제 인스턴스 사용)

실행:
  PYTHONPATH=src pytest tests/test_settlement_service.py -v

검증 대상:
  1. list_settlements() : merchant_id + status 동시 필터
  2. process_settlement(): 존재하지 않는 ID → None
  3. calculate_settlement(): 빈 기간(주문 0건)
"""

import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from settlement.models.models import Order, SettlementStatus
from settlement.services.settlement_service import SettlementService

# ── 픽스처 / 헬퍼 ─────────────────────────────────────────────────────


@pytest.fixture
def svc():
    """테스트마다 새 인스턴스 → 테스트 간 데이터 격리"""
    return SettlementService()


@pytest.fixture
def period():
    """현재 시각 기준 ±1시간 (방금 완료한 주문이 포함되는 기간)"""
    now = datetime.utcnow()
    return now - timedelta(hours=1), now + timedelta(hours=1)


def add_completed_order(svc, merchant_id, amount="10000"):
    """주문 추가 + 완료 처리까지 수행하고 Order 반환"""
    order = Order(
        order_id=f"O-{uuid.uuid4().hex[:8]}",
        merchant_id=merchant_id,
        customer_id="C-001",
        amount=Decimal(amount),
    )
    svc.add_order(order)
    svc.complete_order(order.order_id)
    return order


def make_settlement(svc, merchant_id, period, amount="10000"):
    """완료 주문 1건 생성 후 해당 판매자의 정산 레코드(PENDING) 생성"""
    add_completed_order(svc, merchant_id, amount)
    start, end = period
    return svc.calculate_settlement(merchant_id, start, end)


# ── 1. list_settlements: merchant_id + status 동시 필터 ───────────────


class TestListSettlementsCombinedFilter:
    @pytest.fixture
    def populated(self, svc, period):
        """
        M-A: 정산 2건 (1건 COMPLETED, 1건 PENDING)
        M-B: 정산 1건 (PENDING)
        """
        a_done = make_settlement(svc, "M-A", period)
        a_pending = make_settlement(svc, "M-A", period)
        b_pending = make_settlement(svc, "M-B", period)
        svc.process_settlement(a_done.settlement_id)
        return {"a_done": a_done, "a_pending": a_pending, "b_pending": b_pending}

    def test_merchant_and_status_both_match(self, svc, populated):
        result = svc.list_settlements(merchant_id="M-A", status=SettlementStatus.COMPLETED)
        assert [r.settlement_id for r in result] == [populated["a_done"].settlement_id]

    def test_same_merchant_other_status(self, svc, populated):
        result = svc.list_settlements(merchant_id="M-A", status=SettlementStatus.PENDING)
        assert [r.settlement_id for r in result] == [populated["a_pending"].settlement_id]

    def test_status_matches_but_merchant_differs(self, svc, populated):
        """M-B에는 COMPLETED 정산이 없으므로 빈 목록"""
        result = svc.list_settlements(merchant_id="M-B", status=SettlementStatus.COMPLETED)
        assert result == []

    def test_unknown_merchant_with_valid_status(self, svc, populated):
        result = svc.list_settlements(merchant_id="M-NONE", status=SettlementStatus.PENDING)
        assert result == []

    def test_combined_filter_is_and_not_or(self, svc, populated):
        """AND 조건: 결과는 두 조건을 모두 만족하는 레코드뿐이어야 함"""
        result = svc.list_settlements(merchant_id="M-A", status=SettlementStatus.PENDING)
        assert all(r.merchant_id == "M-A" and r.status == SettlementStatus.PENDING for r in result)
        # M-B의 PENDING, M-A의 COMPLETED는 포함되면 안 됨
        ids = {r.settlement_id for r in result}
        assert populated["b_pending"].settlement_id not in ids
        assert populated["a_done"].settlement_id not in ids

    def test_single_filters_still_work(self, svc, populated):
        """동시 필터가 아닌 단독 필터와 결과 크기 비교 (회귀 확인)"""
        assert len(svc.list_settlements()) == 3
        assert len(svc.list_settlements(merchant_id="M-A")) == 2
        assert len(svc.list_settlements(status=SettlementStatus.PENDING)) == 2
        assert len(svc.list_settlements(status=SettlementStatus.COMPLETED)) == 1


# ── 2. process_settlement: 없는 ID → None ─────────────────────────────


class TestProcessSettlementNotFound:
    def test_returns_none_when_store_is_empty(self, svc):
        assert svc.process_settlement("STL-NOT-EXIST") is None

    def test_returns_none_when_id_not_in_populated_store(self, svc, period):
        make_settlement(svc, "M-A", period)
        assert svc.process_settlement("STL-NOT-EXIST") is None

    def test_not_found_does_not_change_existing_records(self, svc, period):
        record = make_settlement(svc, "M-A", period)

        svc.process_settlement("STL-NOT-EXIST")

        assert record.status == SettlementStatus.PENDING
        assert record.processed_at is None

    def test_existing_id_still_processed(self, svc, period):
        """대조군: 존재하는 ID는 정상 처리되어 None이 아님"""
        record = make_settlement(svc, "M-A", period)

        result = svc.process_settlement(record.settlement_id)

        assert result is not None
        assert result.status == SettlementStatus.COMPLETED


# ── 3. calculate_settlement: 빈 기간 (주문 0건) ───────────────────────


class TestCalculateSettlementEmptyPeriod:
    def test_no_orders_at_all(self, svc, period):
        start, end = period

        rec = svc.calculate_settlement("M-EMPTY", start, end)

        assert rec.order_count == 0
        assert rec.total_sales == Decimal("0")
        assert rec.total_fee == Decimal("0")
        assert rec.net_amount == Decimal("0")

    def test_orders_exist_but_outside_period(self, svc):
        """주문은 있으나 완료 시각이 정산 기간 밖 → 0건"""
        add_completed_order(svc, "M-A", "100000")
        now = datetime.utcnow()

        rec = svc.calculate_settlement("M-A", now - timedelta(days=10), now - timedelta(days=5))

        assert rec.order_count == 0
        assert rec.total_sales == Decimal("0")
        assert rec.net_amount == Decimal("0")

    def test_future_period(self, svc):
        add_completed_order(svc, "M-A", "100000")
        now = datetime.utcnow()

        rec = svc.calculate_settlement("M-A", now + timedelta(days=5), now + timedelta(days=10))

        assert rec.order_count == 0

    def test_orders_only_for_other_merchant(self, svc, period):
        add_completed_order(svc, "M-OTHER", "100000")
        start, end = period

        rec = svc.calculate_settlement("M-A", start, end)

        assert rec.order_count == 0
        assert rec.total_sales == Decimal("0")

    def test_only_pending_orders(self, svc, period):
        """완료 처리되지 않은 주문만 있으면 정산 대상 0건"""
        svc.add_order(
            Order(order_id="PEND-1", merchant_id="M-A", customer_id="C", amount=Decimal("100000"))
        )
        start, end = period

        rec = svc.calculate_settlement("M-A", start, end)

        assert rec.order_count == 0

    def test_empty_period_record_is_still_created_as_pending(self, svc, period):
        """주문이 0건이어도 정산 레코드는 생성·저장되고 PENDING 상태"""
        start, end = period

        rec = svc.calculate_settlement("M-EMPTY", start, end)

        assert rec.status == SettlementStatus.PENDING
        assert rec.merchant_id == "M-EMPTY"
        assert rec.period_start == start
        assert rec.period_end == end
        assert rec.settlement_id.startswith("STL-")
        assert rec in svc.list_settlements(merchant_id="M-EMPTY")

    def test_zero_amount_types_are_decimal(self, svc, period):
        """빈 결과의 금액이 float(0.0)이 아니라 Decimal이어야 함"""
        start, end = period

        rec = svc.calculate_settlement("M-EMPTY", start, end)

        assert isinstance(rec.total_sales, Decimal)
        assert isinstance(rec.total_fee, Decimal)
        assert isinstance(rec.net_amount, Decimal)
