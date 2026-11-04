"""판매자 등급별 수수료 테스트 (시나리오 11.1)"""

from decimal import Decimal

from settlement.models.models import MerchantGrade, Order


class TestMerchantGrade:
    def test_vip_fee_is_2_percent(self):
        o = Order(
            order_id="V1",
            merchant_id="M-VIP",
            customer_id="C",
            amount=Decimal("100000"),
            merchant_grade=MerchantGrade.VIP,
        )
        assert o.fee_amount == Decimal("2000")
        assert o.net_amount == Decimal("98000")

    def test_normal_fee_unchanged(self):
        o = Order(order_id="N1", merchant_id="M", customer_id="C", amount=Decimal("100000"))
        assert o.applied_fee_rate == Decimal("0.03")
