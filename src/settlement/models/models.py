"""
이커머스 정산 시스템 - 데이터 모델
[AI 활용 CI/CD 교육] Day 2·3 실습 워크북 Part 2
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class OrderStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class SettlementStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Order(BaseModel):
    """주문 모델 - 정산의 원천 데이터"""

    order_id: str = Field(..., description="주문 고유 ID")
    merchant_id: str = Field(..., description="판매자 ID")
    customer_id: str = Field(..., description="고객 ID")
    amount: Decimal = Field(..., ge=0, description="주문 금액(원)")
    fee_rate: Decimal = Field(default=Decimal("0.03"), description="수수료율 (기본 3%)")
    status: OrderStatus = Field(default=OrderStatus.PENDING)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None

    @field_validator("amount")
    @classmethod
    def amount_must_be_positive(cls, v):
        if v < 0:
            raise ValueError("주문 금액은 0 이상이어야 합니다.")
        return v

    @property
    def fee_amount(self) -> Decimal:
        """수수료 = 주문금액 × 수수료율 (원 단위 반올림)"""
        return (self.amount * self.fee_rate).quantize(Decimal("1"))

    @property
    def net_amount(self) -> Decimal:
        """실 정산액 = 주문금액 - 수수료"""
        return self.amount - self.fee_amount


class SettlementRecord(BaseModel):
    """정산 레코드 - 판매자별·기간별 정산 결과"""

    settlement_id: str = Field(..., description="정산 고유 ID")
    merchant_id: str = Field(..., description="판매자 ID")
    period_start: datetime = Field(..., description="정산 기간 시작")
    period_end: datetime = Field(..., description="정산 기간 종료")
    total_sales: Decimal = Field(default=Decimal("0"))
    total_fee: Decimal = Field(default=Decimal("0"))
    net_amount: Decimal = Field(default=Decimal("0"))
    order_count: int = Field(default=0)
    status: SettlementStatus = Field(default=SettlementStatus.PENDING)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    processed_at: Optional[datetime] = None
    error_message: Optional[str] = None


class SettlementRequest(BaseModel):
    merchant_id: str
    period_start: datetime
    period_end: datetime


class HealthResponse(BaseModel):
    status: str
    version: str = "1.0.0"
    timestamp: datetime = Field(default_factory=datetime.utcnow)
