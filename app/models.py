import uuid
import enum
from sqlalchemy import Column, String, Integer, DateTime, Enum
from sqlalchemy.sql import func
from .database import Base

class ChargeStatus(str, Enum):
    pending = "pending"
    succeeded = "succeeded"
    failed = "failed"
    flagged = "flagged"
    refunded = "refunded"

class Charge(Base):
    __tablename__ = "charges"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    amount = Column(Integer, nullable=False)
    currency = Column(String(3), nullable=False)
    merchant_id = Column(String, nullable=False)
    idempotency_key = Column(String, nullable=False)
    status = Column(Enum(ChargeStatus), nullable=False, default=ChargeStatus.pending)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    