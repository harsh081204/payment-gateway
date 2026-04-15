from pydantic import BaseModel, Field, ConfigDict, field_validator
from datetime import datetime
from enum import Enum
import re

class ChargeStatus(str, Enum):
    pending = "pending"
    succeeded = "succeeded"
    failed = "failed"
    flagged = "flagged"
    refunded = "refunded"

class ChargeCreate(BaseModel):
    model_config = ConfigDict(strict=True)

    amount: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    merchant_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    card_fingerprint: str | None = None

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v):
        if not re.fullmatch(r"[A-Z]{3}", v):
            raise ValueError("Currency must be 3 uppercase letters (ISO 4217)")
        return v

class ChargeResponse(BaseModel):
    id: str
    amount: int
    currency: str
    merchant_id: str
    status: ChargeStatus
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)