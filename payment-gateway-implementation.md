# Payment Gateway & Fraud Detection API — Full Implementation Guide

> **Goal:** Build a resume-grade payment backend covering idempotency, rate limiting, fraud detection, and refunds — the core of what Stripe does internally.

---

## Table of Contents

- [Project Setup & Environment](#0-project-setup--environment-day-0)
- [Phase 1 — Core API](#phase-1--core-api-estimated-2-days)
- [Phase 2 — Idempotency & Retry Logic](#phase-2--idempotency--retry-logic-estimated-1-day)
- [Phase 3 — Rate Limiter](#phase-3--rate-limiter-estimated-1-day)
- [Phase 4 — Fraud Detection Engine](#phase-4--fraud-detection-engine-estimated-1-day)
- [Phase 5 — Refunds & State Machine](#phase-5--refunds--state-machine-estimated-1-day)
- [Testing Strategy](#testing-strategy)
- [CI/CD Pipeline](#cicd-pipeline-github-actions)
- [Interview Cheat Sheet](#interview-cheat-sheet)

---

## 0. Project Setup & Environment (Day 0)

### Step 0.1 — Create the project structure

Run this to scaffold everything at once:

```bash
mkdir payment-gateway && cd payment-gateway

mkdir -p app/routers app/middleware app/services alembic tests .github/workflows

touch app/__init__.py
touch app/main.py app/models.py app/schemas.py app/database.py app/redis_client.py
touch app/routers/__init__.py app/routers/charges.py app/routers/refunds.py
touch app/middleware/__init__.py app/middleware/rate_limiter.py
touch app/services/__init__.py app/services/idempotency.py
touch app/services/fraud.py app/services/payment_processor.py
touch tests/__init__.py tests/conftest.py
touch tests/test_charges.py tests/test_idempotency.py
touch tests/test_rate_limiter.py tests/test_fraud.py tests/test_refunds.py
touch docker-compose.yml Dockerfile requirements.txt .env .env.example
touch .github/workflows/ci.yml
```

Your final folder structure:

```
payment-gateway/
├── app/
│   ├── main.py                  # FastAPI app factory + startup events
│   ├── models.py                # SQLAlchemy ORM table definitions
│   ├── schemas.py               # Pydantic v2 request + response models
│   ├── database.py              # Postgres async session + engine
│   ├── redis_client.py          # Redis async connection singleton
│   ├── routers/
│   │   ├── charges.py           # POST /charges, GET /charges, GET /charges/:id
│   │   └── refunds.py           # POST /refunds/:charge_id
│   ├── middleware/
│   │   └── rate_limiter.py      # Token bucket middleware
│   └── services/
│       ├── idempotency.py       # Key lookup + cache logic
│       ├── fraud.py             # Velocity check + rule engine
│       └── payment_processor.py # Mock processor + retry logic
├── alembic/                     # DB migration files
├── tests/
│   ├── conftest.py              # Fixtures: test DB, test Redis, test client
│   ├── test_charges.py
│   ├── test_idempotency.py
│   ├── test_rate_limiter.py
│   ├── test_fraud.py
│   └── test_refunds.py
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── .github/workflows/ci.yml
```

---

### Step 0.2 — Write `requirements.txt`

```txt
# Web framework
fastapi==0.111.0
uvicorn[standard]==0.29.0

# Database
sqlalchemy[asyncio]==2.0.30
asyncpg==0.29.0
alembic==1.13.1

# Redis
redis[hiredis]==5.0.4

# Validation
pydantic==2.7.1
pydantic-settings==2.2.1

# Testing
pytest==8.2.0
pytest-asyncio==0.23.7
httpx==0.27.0
fakeredis[aioredis]==2.23.2
pytest-cov==5.0.0

# Utilities
python-dotenv==1.0.1
```

Install everything:

```bash
pip install -r requirements.txt
```

---

### Step 0.3 — Write `docker-compose.yml`

```yaml
version: "3.9"

services:
  api:
    build: .
    ports:
      - "8000:8000"
    env_file:
      - .env
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    volumes:
      - .:/app
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
    networks:
      - payments_network

  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: payments
      POSTGRES_PASSWORD: payments_secret
      POSTGRES_DB: payments_db
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U payments -d payments_db"]
      interval: 5s
      timeout: 5s
      retries: 5
    networks:
      - payments_network

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5
    networks:
      - payments_network

volumes:
  postgres_data:

networks:
  payments_network:
    driver: bridge
```

---

### Step 0.4 — Write `Dockerfile`

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

### Step 0.5 — Write `.env`

```env
# Database
DATABASE_URL=postgresql+asyncpg://payments:payments_secret@db:5432/payments_db
DATABASE_URL_SYNC=postgresql://payments:payments_secret@db:5432/payments_db

# Redis
REDIS_URL=redis://redis:6379/0

# App settings
SECRET_KEY=your-secret-key-change-in-production
ENVIRONMENT=development
DEBUG=true

# Rate limiter
RATE_LIMIT_REQUESTS=100
RATE_LIMIT_WINDOW_SECONDS=60

# Fraud detection
FRAUD_VELOCITY_THRESHOLD=3
FRAUD_VELOCITY_WINDOW_SECONDS=300
```

---

### Step 0.6 — Start Docker and verify

```bash
# Start all services
docker compose up -d

# Check all containers are running
docker compose ps

# Tail logs
docker compose logs -f api

# Connect to Postgres manually to verify
docker compose exec db psql -U payments -d payments_db

# Ping Redis
docker compose exec redis redis-cli ping
# Expected: PONG
```

---

## Phase 1 — Core API (Estimated: 2 days)

### Step 1.1 — Write `app/database.py`

This sets up your async SQLAlchemy engine and session factory.

```python
# app/database.py
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from app.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,       # logs all SQL in development
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,        # verify connections before using them
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,    # objects stay usable after commit
)

class Base(DeclarativeBase):
    pass

# Dependency for FastAPI route injection
async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
```

---

### Step 1.2 — Write `app/config.py`

```python
# app/config.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str
    DATABASE_URL_SYNC: str
    REDIS_URL: str
    SECRET_KEY: str
    ENVIRONMENT: str = "development"
    DEBUG: bool = False
    RATE_LIMIT_REQUESTS: int = 100
    RATE_LIMIT_WINDOW_SECONDS: int = 60
    FRAUD_VELOCITY_THRESHOLD: int = 3
    FRAUD_VELOCITY_WINDOW_SECONDS: int = 300

    class Config:
        env_file = ".env"

settings = Settings()
```

---

### Step 1.3 — Write `app/models.py`

> **Key decision:** Store `amount` as **INTEGER in pence/cents** — never use floats for money. `100` means £1.00 or $1.00.

```python
# app/models.py
import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum
from sqlalchemy import (
    Column, String, Integer, Boolean, DateTime,
    Enum, ForeignKey, Text, Numeric, Index
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class ChargeStatus(str, PyEnum):
    pending   = "pending"
    succeeded = "succeeded"
    failed    = "failed"
    flagged   = "flagged"
    refunded  = "refunded"


class RefundStatus(str, PyEnum):
    pending   = "pending"
    succeeded = "succeeded"
    failed    = "failed"


class Charge(Base):
    __tablename__ = "charges"

    id               = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id      = Column(String(255), nullable=False, index=True)
    amount           = Column(Integer, nullable=False)          # in pence/cents
    currency         = Column(String(3), nullable=False)        # ISO 4217 e.g. "GBP"
    status           = Column(Enum(ChargeStatus), nullable=False, default=ChargeStatus.pending)
    card_fingerprint = Column(String(255), nullable=True, index=True)
    idempotency_key  = Column(String(255), nullable=False, unique=True)
    description      = Column(Text, nullable=True)
    fraud_flagged    = Column(Boolean, default=False)
    fraud_reason     = Column(String(255), nullable=True)
    created_at       = Column(DateTime(timezone=True), default=utcnow)
    updated_at       = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    refunds      = relationship("Refund", back_populates="charge")
    fraud_events = relationship("FraudEvent", back_populates="charge")

    __table_args__ = (
        Index("ix_charges_merchant_status", "merchant_id", "status"),
    )


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    id               = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    idempotency_key  = Column(String(255), nullable=False, unique=True, index=True)
    merchant_id      = Column(String(255), nullable=False)
    response_status  = Column(Integer, nullable=False)
    response_body    = Column(JSONB, nullable=False)
    expires_at       = Column(DateTime(timezone=True), nullable=False)
    created_at       = Column(DateTime(timezone=True), default=utcnow)


class FraudEvent(Base):
    __tablename__ = "fraud_events"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    charge_id       = Column(UUID(as_uuid=True), ForeignKey("charges.id"), nullable=False)
    rule_triggered  = Column(String(255), nullable=False)   # e.g. "velocity_check"
    fraud_score     = Column(Numeric(5, 2), nullable=True)
    metadata        = Column(JSONB, nullable=True)
    created_at      = Column(DateTime(timezone=True), default=utcnow)

    charge = relationship("Charge", back_populates="fraud_events")


class Refund(Base):
    __tablename__ = "refunds"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    charge_id       = Column(UUID(as_uuid=True), ForeignKey("charges.id"), nullable=False)
    amount          = Column(Integer, nullable=False)
    status          = Column(Enum(RefundStatus), nullable=False, default=RefundStatus.pending)
    idempotency_key = Column(String(255), nullable=True, unique=True)
    reason          = Column(Text, nullable=True)
    created_at      = Column(DateTime(timezone=True), default=utcnow)

    charge = relationship("Charge", back_populates="refunds")
```

---

### Step 1.4 — Write `app/schemas.py`

```python
# app/schemas.py
import re
from datetime import datetime
from uuid import UUID
from typing import Optional
from pydantic import BaseModel, field_validator, model_validator


# ── Request Schemas ──────────────────────────────────────────────────────────

class ChargeCreate(BaseModel):
    amount: int                     # must be positive integer (pence/cents)
    currency: str                   # 3-letter ISO code
    merchant_id: str
    idempotency_key: str
    card_fingerprint: Optional[str] = None
    description: Optional[str] = None

    @field_validator("amount")
    @classmethod
    def amount_must_be_positive(cls, v):
        if v <= 0:
            raise ValueError("Amount must be a positive integer (in pence/cents)")
        if v > 99999999:            # max £999,999.99
            raise ValueError("Amount exceeds maximum allowed value")
        return v

    @field_validator("currency")
    @classmethod
    def currency_must_be_valid(cls, v):
        if not re.match(r"^[A-Z]{3}$", v):
            raise ValueError("Currency must be a 3-letter ISO 4217 code (e.g. GBP, USD)")
        return v

    @field_validator("idempotency_key")
    @classmethod
    def idempotency_key_not_empty(cls, v):
        if not v.strip():
            raise ValueError("Idempotency key cannot be empty")
        return v


# ── Response Schemas ─────────────────────────────────────────────────────────

class ChargeResponse(BaseModel):
    id: UUID
    merchant_id: str
    amount: int
    currency: str
    status: str
    card_fingerprint: Optional[str]
    description: Optional[str]
    fraud_flagged: bool
    fraud_reason: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class ChargeListResponse(BaseModel):
    data: list[ChargeResponse]
    count: int
    page: int
    per_page: int


class RefundCreate(BaseModel):
    amount: Optional[int] = None    # None = full refund
    reason: Optional[str] = None
    idempotency_key: Optional[str] = None

    @field_validator("amount")
    @classmethod
    def amount_positive_if_present(cls, v):
        if v is not None and v <= 0:
            raise ValueError("Refund amount must be positive")
        return v


class RefundResponse(BaseModel):
    id: UUID
    charge_id: UUID
    amount: int
    status: str
    reason: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class ErrorResponse(BaseModel):
    detail: str
    code: Optional[str] = None
```

---

### Step 1.5 — Set up Alembic migrations

```bash
# Inside the running api container
docker compose exec api alembic init alembic
```

Edit `alembic/env.py` — replace the `target_metadata` section:

```python
# alembic/env.py  (only the changed parts shown)
from app.models import Base                 # import your models
from app.config import settings

# Replace the existing target_metadata line:
target_metadata = Base.metadata

# Replace the existing get_url() / config.get_main_option line:
def get_url():
    return settings.DATABASE_URL_SYNC       # Alembic needs a sync URL
```

Generate and run your first migration:

```bash
# Auto-generate migration from your models
docker compose exec api alembic revision --autogenerate -m "initial_schema"

# Apply it
docker compose exec api alembic upgrade head

# Verify tables were created
docker compose exec db psql -U payments -d payments_db -c "\dt"
```

You should see: `charges`, `idempotency_keys`, `fraud_events`, `refunds`.

---

### Step 1.6 — Write the payment processor service (mock)

```python
# app/services/payment_processor.py
import asyncio
import random
from app.models import ChargeStatus


class PaymentProcessorError(Exception):
    """Raised when the processor returns a 5xx-style error (retryable)."""
    pass


class PaymentDeclinedError(Exception):
    """Raised when the card is declined (4xx-style, not retryable)."""
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


async def process_charge(amount: int, currency: str, card_fingerprint: str) -> ChargeStatus:
    """
    Simulates a payment processor.
    - 85% succeed
    - 10% fail (card declined — not retryable)
    - 5%  raise PaymentProcessorError (network error — retryable)
    """
    await asyncio.sleep(0.05)           # simulate network latency

    roll = random.random()
    if roll < 0.85:
        return ChargeStatus.succeeded
    elif roll < 0.95:
        raise PaymentDeclinedError("insufficient_funds")
    else:
        raise PaymentProcessorError("upstream_timeout")


async def process_charge_with_retry(
    amount: int,
    currency: str,
    card_fingerprint: str,
    max_retries: int = 3
) -> ChargeStatus:
    """
    Wraps process_charge with exponential backoff.
    Only retries PaymentProcessorError (5xx), never PaymentDeclinedError (4xx).
    Backoff: 1s, 2s, 4s
    """
    for attempt in range(max_retries + 1):
        try:
            return await process_charge(amount, currency, card_fingerprint)
        except PaymentProcessorError:
            if attempt == max_retries:
                raise                   # exhausted all retries
            wait = 2 ** attempt         # 1, 2, 4 seconds
            await asyncio.sleep(wait)
        except PaymentDeclinedError:
            raise                       # never retry 4xx
```

---

### Step 1.7 — Write `app/routers/charges.py`

```python
# app/routers/charges.py
from uuid import UUID
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.database import get_db
from app.models import Charge, ChargeStatus
from app.schemas import ChargeCreate, ChargeResponse, ChargeListResponse
from app.services.payment_processor import (
    process_charge_with_retry, PaymentDeclinedError, PaymentProcessorError
)

router = APIRouter(prefix="/charges", tags=["charges"])


@router.post("", response_model=ChargeResponse, status_code=201)
async def create_charge(
    body: ChargeCreate,
    db: AsyncSession = Depends(get_db),
):
    # Create charge in pending state
    charge = Charge(
        merchant_id=body.merchant_id,
        amount=body.amount,
        currency=body.currency,
        card_fingerprint=body.card_fingerprint,
        idempotency_key=body.idempotency_key,
        description=body.description,
        status=ChargeStatus.pending,
    )
    db.add(charge)
    await db.flush()                # get the UUID without committing

    # Process payment
    try:
        status = await process_charge_with_retry(
            amount=body.amount,
            currency=body.currency,
            card_fingerprint=body.card_fingerprint or "",
        )
        charge.status = status

    except PaymentDeclinedError as e:
        charge.status = ChargeStatus.failed
        charge.fraud_reason = e.reason

    except PaymentProcessorError:
        charge.status = ChargeStatus.failed
        charge.fraud_reason = "processor_unavailable"

    await db.commit()
    await db.refresh(charge)
    return charge


@router.get("/{charge_id}", response_model=ChargeResponse)
async def get_charge(
    charge_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Charge).where(Charge.id == charge_id))
    charge = result.scalar_one_or_none()
    if not charge:
        raise HTTPException(status_code=404, detail="Charge not found")
    return charge


@router.get("", response_model=ChargeListResponse)
async def list_charges(
    merchant_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    filters = []
    if merchant_id:
        filters.append(Charge.merchant_id == merchant_id)
    if status:
        filters.append(Charge.status == status)

    query = select(Charge).order_by(Charge.created_at.desc())
    if filters:
        query = query.where(and_(*filters))

    # Pagination
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)

    result = await db.execute(query)
    charges = result.scalars().all()

    return ChargeListResponse(
        data=charges,
        count=len(charges),
        page=page,
        per_page=per_page,
    )
```

---

### Step 1.8 — Wire up `app/main.py`

```python
# app/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import engine, Base
from app.routers import charges, refunds


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: nothing needed (Alembic handles migrations)
    yield
    # Shutdown: dispose engine
    await engine.dispose()


app = FastAPI(
    title="Payment Gateway API",
    description="Stripe-style payment processing with fraud detection",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(charges.router)
app.include_router(refunds.router)


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "payment-gateway"}
```

---

### Step 1.9 — Smoke test Phase 1

```bash
# Start the server
docker compose up -d

# Create a charge
curl -X POST http://localhost:8000/charges \
  -H "Content-Type: application/json" \
  -d '{
    "amount": 1000,
    "currency": "GBP",
    "merchant_id": "merchant_abc",
    "idempotency_key": "test-key-001",
    "card_fingerprint": "card_fingerprint_xyz"
  }'

# Expected: 201 with charge object

# Get charge by ID (replace <id> with the returned id)
curl http://localhost:8000/charges/<id>

# List charges for merchant
curl "http://localhost:8000/charges?merchant_id=merchant_abc&status=succeeded"
```

---

## Phase 2 — Idempotency & Retry Logic (Estimated: 1 day)

### Step 2.1 — Understand what idempotency means here

When a client sends `POST /charges` with an `idempotency_key`, and then sends the **exact same request again** (e.g. due to a network timeout), the server must:

1. Return the **same response** as the first request
2. **Not create a duplicate charge**
3. **Not run fraud checks again**
4. **Not call the payment processor again**

This is exactly how Stripe's API works. You store the response and replay it.

---

### Step 2.2 — Write `app/services/idempotency.py`

```python
# app/services/idempotency.py
from datetime import datetime, timezone, timedelta
from typing import Optional, Callable, Awaitable, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import IdempotencyKey


KEY_TTL_HOURS = 24


def utcnow():
    return datetime.now(timezone.utc)


async def get_cached_response(
    db: AsyncSession,
    idempotency_key: str,
    merchant_id: str,
) -> Optional[IdempotencyKey]:
    """
    Returns the cached IdempotencyKey record if it exists and hasn't expired.
    Returns None if not found or expired.
    """
    result = await db.execute(
        select(IdempotencyKey).where(
            IdempotencyKey.idempotency_key == idempotency_key,
            IdempotencyKey.merchant_id == merchant_id,
        )
    )
    record = result.scalar_one_or_none()

    if record is None:
        return None

    if record.expires_at < utcnow():
        await db.delete(record)
        await db.commit()
        return None

    return record


async def save_response(
    db: AsyncSession,
    idempotency_key: str,
    merchant_id: str,
    response_status: int,
    response_body: dict,
) -> IdempotencyKey:
    """Persists the response so future duplicate requests can replay it."""
    record = IdempotencyKey(
        idempotency_key=idempotency_key,
        merchant_id=merchant_id,
        response_status=response_status,
        response_body=response_body,
        expires_at=utcnow() + timedelta(hours=KEY_TTL_HOURS),
    )
    db.add(record)
    await db.flush()
    return record
```

---

### Step 2.3 — Integrate idempotency into the charge route

Update `app/routers/charges.py` — wrap the create logic:

```python
# app/routers/charges.py  (updated POST handler)
from fastapi.responses import JSONResponse
from app.services.idempotency import get_cached_response, save_response

@router.post("", response_model=ChargeResponse, status_code=201)
async def create_charge(
    body: ChargeCreate,
    db: AsyncSession = Depends(get_db),
):
    # ── Step 1: Check idempotency cache ─────────────────────────────────────
    cached = await get_cached_response(db, body.idempotency_key, body.merchant_id)
    if cached:
        # Return the exact same response as the original request — no processing
        return JSONResponse(
            content=cached.response_body,
            status_code=cached.response_status,
        )

    # ── Step 2: Fraud check (Phase 4 will add this) ─────────────────────────
    # fraud_flagged = await check_fraud(redis, body.card_fingerprint, ...)

    # ── Step 3: Create charge + process payment ──────────────────────────────
    charge = Charge(
        merchant_id=body.merchant_id,
        amount=body.amount,
        currency=body.currency,
        card_fingerprint=body.card_fingerprint,
        idempotency_key=body.idempotency_key,
        description=body.description,
        status=ChargeStatus.pending,
    )
    db.add(charge)
    await db.flush()

    try:
        status = await process_charge_with_retry(
            amount=body.amount,
            currency=body.currency,
            card_fingerprint=body.card_fingerprint or "",
        )
        charge.status = status
        response_status_code = 201

    except PaymentDeclinedError as e:
        charge.status = ChargeStatus.failed
        charge.fraud_reason = e.reason
        response_status_code = 201       # still 201, but status=failed

    except PaymentProcessorError:
        charge.status = ChargeStatus.failed
        charge.fraud_reason = "processor_unavailable"
        response_status_code = 201

    await db.flush()

    # ── Step 4: Cache the response ───────────────────────────────────────────
    response_body = {
        "id": str(charge.id),
        "merchant_id": charge.merchant_id,
        "amount": charge.amount,
        "currency": charge.currency,
        "status": charge.status.value,
        "card_fingerprint": charge.card_fingerprint,
        "description": charge.description,
        "fraud_flagged": charge.fraud_flagged,
        "fraud_reason": charge.fraud_reason,
        "created_at": charge.created_at.isoformat(),
    }

    await save_response(
        db=db,
        idempotency_key=body.idempotency_key,
        merchant_id=body.merchant_id,
        response_status=response_status_code,
        response_body=response_body,
    )

    await db.commit()
    return JSONResponse(content=response_body, status_code=response_status_code)
```

---

### Step 2.4 — Test idempotency manually

```bash
# Send the same request twice with the same idempotency_key
BODY='{"amount":500,"currency":"GBP","merchant_id":"merch_1","idempotency_key":"idem-test-001","card_fingerprint":"card_abc"}'

curl -X POST http://localhost:8000/charges \
  -H "Content-Type: application/json" \
  -d "$BODY"

# Send AGAIN — must return identical response, no new charge
curl -X POST http://localhost:8000/charges \
  -H "Content-Type: application/json" \
  -d "$BODY"

# Verify only ONE charge exists for this merchant
curl "http://localhost:8000/charges?merchant_id=merch_1"
# count should be 1, not 2
```

---

### Step 2.5 — Write the retry test

```python
# tests/test_idempotency.py
import pytest
import asyncio

@pytest.mark.asyncio
async def test_duplicate_key_returns_same_response(client):
    body = {
        "amount": 1000,
        "currency": "GBP",
        "merchant_id": "merch_test",
        "idempotency_key": "unique-key-123",
        "card_fingerprint": "fp_abc",
    }

    r1 = await client.post("/charges", json=body)
    r2 = await client.post("/charges", json=body)   # duplicate

    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]        # same charge ID


@pytest.mark.asyncio
async def test_concurrent_duplicate_requests_safe(client):
    """Simulates two requests arriving simultaneously with the same key."""
    body = {
        "amount": 2000,
        "currency": "USD",
        "merchant_id": "merch_concurrent",
        "idempotency_key": "concurrent-key-001",
        "card_fingerprint": "fp_concurrent",
    }

    results = await asyncio.gather(
        client.post("/charges", json=body),
        client.post("/charges", json=body),
    )

    ids = [r.json()["id"] for r in results]
    assert ids[0] == ids[1]                          # both must return same charge
```

---

## Phase 3 — Rate Limiter (Estimated: 1 day)

### Step 3.1 — Write `app/redis_client.py`

```python
# app/redis_client.py
import redis.asyncio as aioredis
from app.config import settings

_redis_client = None

async def get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis_client

async def close_redis():
    global _redis_client
    if _redis_client:
        await _redis_client.aclose()
        _redis_client = None
```

Update `app/main.py` lifespan:

```python
from app.redis_client import close_redis

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await engine.dispose()
    await close_redis()
```

---

### Step 3.2 — Write the Lua token bucket script

Create `app/middleware/rate_limiter_script.lua`:

```lua
-- Token bucket rate limiter
-- KEYS[1] = Redis key for this merchant (e.g. "ratelimit:merchant_abc")
-- ARGV[1] = bucket capacity (max tokens)
-- ARGV[2] = refill rate (tokens per second)
-- ARGV[3] = current timestamp in milliseconds

local key         = KEYS[1]
local capacity    = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])   -- tokens/second
local now_ms      = tonumber(ARGV[3])

-- Load current state
local data        = redis.call("HMGET", key, "tokens", "last_refill_ms")
local tokens      = tonumber(data[1]) or capacity
local last_refill = tonumber(data[2]) or now_ms

-- Refill tokens based on elapsed time
local elapsed_seconds = (now_ms - last_refill) / 1000.0
local new_tokens = math.min(capacity, tokens + elapsed_seconds * refill_rate)

-- Check if we can consume one token
if new_tokens < 1 then
    -- Calculate ms until next token is available
    local ms_until_token = math.ceil((1 - new_tokens) / refill_rate * 1000)
    return {0, ms_until_token}   -- {denied, retry_after_ms}
end

-- Consume one token and save state
redis.call("HMSET", key, "tokens", new_tokens - 1, "last_refill_ms", now_ms)
redis.call("PEXPIRE", key, 120000)   -- expire key after 2 min of inactivity

return {1, 0}    -- {allowed, 0}
```

---

### Step 3.3 — Write `app/middleware/rate_limiter.py`

```python
# app/middleware/rate_limiter.py
import time
import math
from pathlib import Path
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.redis_client import get_redis
from app.config import settings

# Load Lua script at import time
LUA_SCRIPT_PATH = Path(__file__).parent / "rate_limiter_script.lua"
LUA_SCRIPT = LUA_SCRIPT_PATH.read_text()


class RateLimiterMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        # Only rate-limit charge creation (the expensive operation)
        if not (request.method == "POST" and request.url.path == "/charges"):
            return await call_next(request)

        merchant_id = await self._extract_merchant_id(request)
        if not merchant_id:
            return await call_next(request)

        redis = await get_redis()

        capacity    = settings.RATE_LIMIT_REQUESTS          # 100
        window_secs = settings.RATE_LIMIT_WINDOW_SECONDS    # 60
        refill_rate = capacity / window_secs                 # tokens per second

        key     = f"ratelimit:{merchant_id}"
        now_ms  = int(time.time() * 1000)

        # Run atomic Lua script
        result = await redis.eval(
            LUA_SCRIPT,
            1,                          # number of keys
            key,                        # KEYS[1]
            capacity,                   # ARGV[1]
            refill_rate,                # ARGV[2]
            now_ms,                     # ARGV[3]
        )

        allowed, retry_after_ms = int(result[0]), int(result[1])

        if not allowed:
            retry_after_seconds = math.ceil(retry_after_ms / 1000)
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded",
                    "code": "rate_limit_exceeded",
                    "retry_after": retry_after_seconds,
                },
                headers={"Retry-After": str(retry_after_seconds)},
            )

        return await call_next(request)

    async def _extract_merchant_id(self, request: Request) -> str | None:
        """Read merchant_id from the request body without consuming the stream."""
        try:
            body = await request.body()
            import json
            data = json.loads(body)
            return data.get("merchant_id")
        except Exception:
            return None
```

---

### Step 3.4 — Register the middleware in `app/main.py`

```python
# app/main.py  (add this after creating the FastAPI app)
from app.middleware.rate_limiter import RateLimiterMiddleware

app.add_middleware(RateLimiterMiddleware)
```

> **Important:** Middleware is applied in reverse order of registration. Add `RateLimiterMiddleware` before `CORSMiddleware` so it runs first.

---

### Step 3.5 — Test the rate limiter

```bash
# Fire 105 requests at the same merchant — the last 5 should 429
for i in $(seq 1 105); do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8000/charges \
    -H "Content-Type: application/json" \
    -d "{\"amount\":100,\"currency\":\"GBP\",\"merchant_id\":\"merch_ratelimit\",\"idempotency_key\":\"key-$i\",\"card_fingerprint\":\"fp\"}")
  echo "Request $i: $STATUS"
done
```

---

### Step 3.6 — Write the rate limiter test

```python
# tests/test_rate_limiter.py
import pytest

@pytest.mark.asyncio
async def test_rate_limit_returns_429_after_threshold(client, fake_redis):
    """Sends RATE_LIMIT_REQUESTS + 1 requests; the last must be 429."""
    limit = 5   # use a small limit in tests via override

    responses = []
    for i in range(limit + 1):
        r = await client.post("/charges", json={
            "amount": 100,
            "currency": "GBP",
            "merchant_id": "merch_limited",
            "idempotency_key": f"rl-key-{i}",
            "card_fingerprint": "fp_test",
        })
        responses.append(r.status_code)

    assert responses[-1] == 429


@pytest.mark.asyncio
async def test_rate_limit_includes_retry_after_header(client, fake_redis):
    """429 response must include Retry-After header."""
    limit = 5
    for i in range(limit + 1):
        r = await client.post("/charges", json={
            "amount": 100,
            "currency": "GBP",
            "merchant_id": "merch_header_test",
            "idempotency_key": f"hdr-key-{i}",
            "card_fingerprint": "fp",
        })

    assert r.status_code == 429
    assert "Retry-After" in r.headers
    assert int(r.headers["Retry-After"]) > 0


@pytest.mark.asyncio
async def test_different_merchants_have_separate_buckets(client, fake_redis):
    """Rate limits are per merchant — merchant B should not be affected by merchant A."""
    limit = 5
    for i in range(limit + 1):
        await client.post("/charges", json={
            "amount": 100,
            "currency": "GBP",
            "merchant_id": "merch_a",
            "idempotency_key": f"a-key-{i}",
            "card_fingerprint": "fp",
        })

    # merchant_b should still be under limit
    r = await client.post("/charges", json={
        "amount": 100,
        "currency": "GBP",
        "merchant_id": "merch_b",
        "idempotency_key": "b-key-0",
        "card_fingerprint": "fp",
    })
    assert r.status_code == 201
```

---

## Phase 4 — Fraud Detection Engine (Estimated: 1 day)

### Step 4.1 — Understand the velocity check

The velocity check answers: **"Has this card been used 3+ times in the last 5 minutes?"**

Redis sorted sets are perfect here:
- **Key:** `fraud:velocity:{card_fingerprint}`
- **Member:** charge UUID
- **Score:** Unix timestamp of the charge

To check velocity:
1. `ZADD` — add current charge with current timestamp as score
2. `ZREMRANGEBYSCORE` — remove entries older than 5 minutes
3. `ZCARD` — count remaining entries
4. If count >= 3 → flag as fraud

All four commands run in a single **pipeline** (atomic enough for this use case).

---

### Step 4.2 — Write the fraud rule base class

```python
# app/services/fraud.py
import time
import abc
from dataclasses import dataclass
from typing import Optional
import redis.asyncio as aioredis


@dataclass
class FraudCheckResult:
    is_fraud: bool
    rule_triggered: Optional[str] = None
    fraud_score: float = 0.0
    metadata: Optional[dict] = None


class FraudRule(abc.ABC):
    """Base class for all fraud rules — strategy pattern."""

    @abc.abstractmethod
    async def check(
        self,
        redis: aioredis.Redis,
        card_fingerprint: str,
        merchant_id: str,
        amount: int,
        charge_id: str,
    ) -> FraudCheckResult:
        raise NotImplementedError
```

---

### Step 4.3 — Write the velocity check rule

```python
# app/services/fraud.py  (continued)

class VelocityRule(FraudRule):
    """
    Flags a card if it has been charged 3+ times in a 5-minute rolling window.
    Uses a Redis sorted set: score = timestamp, member = charge_id.
    """

    def __init__(self, threshold: int = 3, window_seconds: int = 300):
        self.threshold      = threshold       # 3 charges
        self.window_seconds = window_seconds  # 5 minutes

    async def check(
        self,
        redis: aioredis.Redis,
        card_fingerprint: str,
        merchant_id: str,
        amount: int,
        charge_id: str,
    ) -> FraudCheckResult:
        if not card_fingerprint:
            return FraudCheckResult(is_fraud=False)

        key = f"fraud:velocity:{card_fingerprint}"
        now = time.time()
        window_start = now - self.window_seconds

        # Atomic pipeline: add current charge, prune old ones, count recent ones
        pipe = redis.pipeline()
        pipe.zadd(key, {charge_id: now})
        pipe.zremrangebyscore(key, 0, window_start)
        pipe.zcard(key)
        pipe.expire(key, self.window_seconds * 2)   # clean up after 10 min
        results = await pipe.execute()

        count = int(results[2])
        is_fraud = count >= self.threshold

        return FraudCheckResult(
            is_fraud=is_fraud,
            rule_triggered="velocity_check" if is_fraud else None,
            fraud_score=min(count / self.threshold, 1.0),
            metadata={
                "card_fingerprint": card_fingerprint,
                "charges_in_window": count,
                "threshold": self.threshold,
                "window_seconds": self.window_seconds,
            },
        )
```

---

### Step 4.4 — Write the fraud engine orchestrator

```python
# app/services/fraud.py  (continued)

class FraudEngine:
    """
    Runs all registered rules in order.
    Returns the first fraud hit, or a clean result.
    """

    def __init__(self, rules: list[FraudRule]):
        self.rules = rules

    async def evaluate(
        self,
        redis: aioredis.Redis,
        card_fingerprint: str,
        merchant_id: str,
        amount: int,
        charge_id: str,
    ) -> FraudCheckResult:
        for rule in self.rules:
            result = await rule.check(
                redis=redis,
                card_fingerprint=card_fingerprint,
                merchant_id=merchant_id,
                amount=amount,
                charge_id=charge_id,
            )
            if result.is_fraud:
                return result

        return FraudCheckResult(is_fraud=False)


# Default engine with all rules
def get_fraud_engine() -> FraudEngine:
    return FraudEngine(rules=[
        VelocityRule(threshold=3, window_seconds=300),
        # Add more rules here: AmountThresholdRule, GeoRule, etc.
    ])
```

---

### Step 4.5 — Wire fraud engine into the charge route

Update `app/routers/charges.py`:

```python
# app/routers/charges.py  (updated create_charge handler)
from app.redis_client import get_redis
from app.services.fraud import get_fraud_engine, FraudCheckResult
from app.models import FraudEvent

@router.post("", response_model=ChargeResponse, status_code=201)
async def create_charge(
    body: ChargeCreate,
    db: AsyncSession = Depends(get_db),
):
    # ── Idempotency check ────────────────────────────────────────────────────
    cached = await get_cached_response(db, body.idempotency_key, body.merchant_id)
    if cached:
        return JSONResponse(content=cached.response_body, status_code=cached.response_status)

    # ── Create charge (pending) ──────────────────────────────────────────────
    charge = Charge(
        merchant_id=body.merchant_id,
        amount=body.amount,
        currency=body.currency,
        card_fingerprint=body.card_fingerprint,
        idempotency_key=body.idempotency_key,
        description=body.description,
        status=ChargeStatus.pending,
    )
    db.add(charge)
    await db.flush()

    # ── Fraud check ──────────────────────────────────────────────────────────
    redis = await get_redis()
    fraud_engine = get_fraud_engine()

    fraud_result: FraudCheckResult = await fraud_engine.evaluate(
        redis=redis,
        card_fingerprint=body.card_fingerprint or "",
        merchant_id=body.merchant_id,
        amount=body.amount,
        charge_id=str(charge.id),
    )

    if fraud_result.is_fraud:
        charge.status      = ChargeStatus.flagged
        charge.fraud_flagged = True
        charge.fraud_reason  = fraud_result.rule_triggered

        # Log the fraud event
        fraud_event = FraudEvent(
            charge_id=charge.id,
            rule_triggered=fraud_result.rule_triggered,
            fraud_score=fraud_result.fraud_score,
            metadata=fraud_result.metadata,
        )
        db.add(fraud_event)

    else:
        # ── Process payment (only if not flagged) ────────────────────────────
        try:
            status = await process_charge_with_retry(
                amount=body.amount,
                currency=body.currency,
                card_fingerprint=body.card_fingerprint or "",
            )
            charge.status = status

        except PaymentDeclinedError as e:
            charge.status = ChargeStatus.failed
            charge.fraud_reason = e.reason

        except PaymentProcessorError:
            charge.status = ChargeStatus.failed
            charge.fraud_reason = "processor_unavailable"

    # ── Cache + commit ───────────────────────────────────────────────────────
    response_body = ChargeResponse.model_validate(charge).model_dump(mode="json")
    await save_response(db, body.idempotency_key, body.merchant_id, 201, response_body)
    await db.commit()

    return JSONResponse(content=response_body, status_code=201)
```

---

### Step 4.6 — Test the fraud engine

```python
# tests/test_fraud.py
import pytest
import asyncio

@pytest.mark.asyncio
async def test_third_charge_same_card_is_flagged(client):
    """After 2 clean charges, the 3rd with the same card fingerprint must be flagged."""
    fingerprint = "suspect_card_fp"

    for i in range(2):
        r = await client.post("/charges", json={
            "amount": 500,
            "currency": "GBP",
            "merchant_id": "merch_fraud_test",
            "idempotency_key": f"fraud-key-{i}",
            "card_fingerprint": fingerprint,
        })
        assert r.json()["status"] in ("succeeded", "failed")   # not flagged yet

    # 3rd charge — must be flagged
    r = await client.post("/charges", json={
        "amount": 500,
        "currency": "GBP",
        "merchant_id": "merch_fraud_test",
        "idempotency_key": "fraud-key-2",
        "card_fingerprint": fingerprint,
    })
    assert r.json()["status"] == "flagged"
    assert r.json()["fraud_flagged"] is True


@pytest.mark.asyncio
async def test_different_cards_not_flagged(client):
    """Different card fingerprints must not affect each other's velocity counter."""
    for i in range(3):
        r = await client.post("/charges", json={
            "amount": 500,
            "currency": "GBP",
            "merchant_id": "merch_cards_test",
            "idempotency_key": f"diff-card-key-{i}",
            "card_fingerprint": f"unique_card_{i}",     # different card each time
        })
        assert r.json()["status"] != "flagged"


@pytest.mark.asyncio
async def test_no_card_fingerprint_skips_fraud_check(client):
    """Charges without a card fingerprint bypass fraud checks."""
    r = await client.post("/charges", json={
        "amount": 500,
        "currency": "GBP",
        "merchant_id": "merch_no_fp",
        "idempotency_key": "no-fp-key",
        # card_fingerprint omitted
    })
    assert r.json()["status"] in ("succeeded", "failed")   # not flagged
```

---

## Phase 5 — Refunds & State Machine (Estimated: 1 day)

### Step 5.1 — Understand the state machine

Valid transitions:

```
pending   → succeeded  (processor confirms)
pending   → failed     (processor declines)
pending   → flagged    (fraud engine triggers)
succeeded → refunded   (POST /refunds — the ONLY valid refund path)

INVALID:
failed    → refunded   (422 Unprocessable Entity)
flagged   → refunded   (422 Unprocessable Entity)
pending   → refunded   (422 Unprocessable Entity)
```

---

### Step 5.2 — Write `app/routers/refunds.py`

```python
# app/routers/refunds.py
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import Charge, ChargeStatus, Refund, RefundStatus
from app.schemas import RefundCreate, RefundResponse
from app.services.idempotency import get_cached_response, save_response

router = APIRouter(prefix="/refunds", tags=["refunds"])

# Only these statuses can be refunded
REFUNDABLE_STATUSES = {ChargeStatus.succeeded}


@router.post("/{charge_id}", response_model=RefundResponse, status_code=201)
async def create_refund(
    charge_id: UUID,
    body: RefundCreate,
    db: AsyncSession = Depends(get_db),
):
    # ── Load the charge ──────────────────────────────────────────────────────
    result = await db.execute(select(Charge).where(Charge.id == charge_id))
    charge = result.scalar_one_or_none()

    if not charge:
        raise HTTPException(status_code=404, detail="Charge not found")

    # ── Idempotency (optional key on refunds) ────────────────────────────────
    if body.idempotency_key:
        cached = await get_cached_response(db, body.idempotency_key, charge.merchant_id)
        if cached:
            from fastapi.responses import JSONResponse
            return JSONResponse(content=cached.response_body, status_code=cached.response_status)

    # ── State machine enforcement ─────────────────────────────────────────────
    if charge.status not in REFUNDABLE_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Cannot refund a charge with status: {charge.status.value}. "
                   f"Only 'succeeded' charges can be refunded.",
        )

    # ── Amount validation ─────────────────────────────────────────────────────
    refund_amount = body.amount or charge.amount    # None = full refund

    # Check we're not over-refunding (sum of existing refunds + new refund)
    existing_refunds_result = await db.execute(
        select(Refund).where(
            Refund.charge_id == charge_id,
            Refund.status == RefundStatus.succeeded,
        )
    )
    existing_refunds = existing_refunds_result.scalars().all()
    already_refunded = sum(r.amount for r in existing_refunds)

    if already_refunded + refund_amount > charge.amount:
        raise HTTPException(
            status_code=422,
            detail=f"Refund amount ({refund_amount}) would exceed charge amount "
                   f"({charge.amount}). Already refunded: {already_refunded}.",
        )

    # ── Create refund ─────────────────────────────────────────────────────────
    refund = Refund(
        charge_id=charge.id,
        amount=refund_amount,
        status=RefundStatus.succeeded,          # mock: always succeeds
        reason=body.reason,
        idempotency_key=body.idempotency_key,
    )
    db.add(refund)

    # Update charge status if fully refunded
    if already_refunded + refund_amount == charge.amount:
        charge.status = ChargeStatus.refunded

    await db.flush()

    # ── Cache + commit ────────────────────────────────────────────────────────
    response_body = RefundResponse.model_validate(refund).model_dump(mode="json")

    if body.idempotency_key:
        await save_response(db, body.idempotency_key, charge.merchant_id, 201, response_body)

    await db.commit()
    return refund
```

---

### Step 5.3 — Test the refund state machine

```python
# tests/test_refunds.py
import pytest

@pytest.mark.asyncio
async def test_refund_succeeded_charge(client):
    """A succeeded charge can be refunded."""
    # Create and ensure succeeded charge
    r = await client.post("/charges", json={...})
    charge_id = r.json()["id"]

    # Only test if it succeeded (random mock, so skip if not)
    if r.json()["status"] != "succeeded":
        pytest.skip("Charge did not succeed in mock processor")

    refund_r = await client.post(f"/refunds/{charge_id}", json={
        "reason": "customer_request",
    })
    assert refund_r.status_code == 201
    assert refund_r.json()["amount"] == r.json()["amount"]


@pytest.mark.asyncio
async def test_cannot_refund_failed_charge(client):
    """Refunding a failed charge must return 422."""
    # Use a patched processor to force a failure
    r = await client.post("/charges", json={...})
    if r.json()["status"] != "failed":
        pytest.skip("Charge did not fail in mock processor")

    refund_r = await client.post(f"/refunds/{r.json()['id']}", json={})
    assert refund_r.status_code == 422
    assert "Cannot refund" in refund_r.json()["detail"]


@pytest.mark.asyncio
async def test_cannot_refund_flagged_charge(client):
    """Refunding a fraud-flagged charge must return 422."""
    # Create 3 charges with same fingerprint to trigger flagging
    fp = "refund_fraud_fp"
    charge_id = None
    for i in range(3):
        r = await client.post("/charges", json={
            "amount": 500, "currency": "GBP",
            "merchant_id": "merch_refund_fraud",
            "idempotency_key": f"rf-key-{i}",
            "card_fingerprint": fp,
        })
        charge_id = r.json()["id"]

    # Last charge should be flagged
    refund_r = await client.post(f"/refunds/{charge_id}", json={})
    assert refund_r.status_code == 422


@pytest.mark.asyncio
async def test_partial_refund(client):
    """Partial refund for less than the charge amount."""
    r = await client.post("/charges", json={
        "amount": 1000, "currency": "GBP",
        "merchant_id": "merch_partial",
        "idempotency_key": "partial-charge-1",
        "card_fingerprint": "fp_partial",
    })
    if r.json()["status"] != "succeeded":
        pytest.skip()

    refund_r = await client.post(f"/refunds/{r.json()['id']}", json={
        "amount": 400,   # partial refund of £4.00 from £10.00
    })
    assert refund_r.status_code == 201
    assert refund_r.json()["amount"] == 400


@pytest.mark.asyncio
async def test_cannot_over_refund(client):
    """Refunding more than the charge amount must return 422."""
    r = await client.post("/charges", json={
        "amount": 500, "currency": "GBP",
        "merchant_id": "merch_over",
        "idempotency_key": "over-charge-1",
        "card_fingerprint": "fp_over",
    })
    if r.json()["status"] != "succeeded":
        pytest.skip()

    refund_r = await client.post(f"/refunds/{r.json()['id']}", json={
        "amount": 999,   # more than 500
    })
    assert refund_r.status_code == 422
```

---

## Testing Strategy

### Step T.1 — Write `tests/conftest.py`

```python
# tests/conftest.py
import pytest
import pytest_asyncio
import fakeredis.aioredis
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.main import app
from app.database import Base, get_db
from app.redis_client import get_redis

# Use SQLite for fast in-memory tests (no Docker needed in CI)
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="function")
async def db_session():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def fake_redis():
    """In-memory Redis replacement — no Docker needed."""
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest_asyncio.fixture(scope="function")
async def client(db_session, fake_redis):
    """Test HTTP client with DB and Redis overridden."""

    async def override_db():
        yield db_session

    async def override_redis():
        return fake_redis

    app.dependency_overrides[get_db]    = override_db
    app.dependency_overrides[get_redis] = override_redis

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()
```

---

### Step T.2 — Run your tests

```bash
# Run all tests
docker compose exec api pytest

# With coverage report
docker compose exec api pytest --cov=app --cov-report=term-missing

# Run a specific test file
docker compose exec api pytest tests/test_fraud.py -v

# Run with detailed output
docker compose exec api pytest -v --tb=short

# Only run tests matching a keyword
docker compose exec api pytest -k "fraud" -v

# Generate HTML coverage report
docker compose exec api pytest --cov=app --cov-report=html
# Open htmlcov/index.html in browser
```

---

### Step T.3 — Check coverage gate

```bash
# Fail if coverage drops below 85%
docker compose exec api pytest --cov=app --cov-fail-under=85
```

---

## CI/CD Pipeline (GitHub Actions)

### Step CI.1 — Write `.github/workflows/ci.yml`

```yaml
name: CI

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  lint:
    name: Lint
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install linters
        run: pip install ruff mypy

      - name: Run ruff (linter + formatter check)
        run: ruff check app/ && ruff format --check app/

      - name: Run mypy (type check)
        run: mypy app/ --ignore-missing-imports

  test:
    name: Test
    runs-on: ubuntu-latest
    needs: lint

    services:
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_USER: payments
          POSTGRES_PASSWORD: payments_secret
          POSTGRES_DB: payments_test
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 5s
          --health-timeout 5s
          --health-retries 5

      redis:
        image: redis:7-alpine
        ports:
          - 6379:6379
        options: >-
          --health-cmd "redis-cli ping"
          --health-interval 5s
          --health-timeout 3s
          --health-retries 5

    env:
      DATABASE_URL: postgresql+asyncpg://payments:payments_secret@localhost:5432/payments_test
      DATABASE_URL_SYNC: postgresql://payments:payments_secret@localhost:5432/payments_test
      REDIS_URL: redis://localhost:6379/0
      SECRET_KEY: test-secret-key
      ENVIRONMENT: test

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: "pip"

      - name: Install dependencies
        run: pip install -r requirements.txt aiosqlite

      - name: Run migrations
        run: alembic upgrade head

      - name: Run tests with coverage
        run: pytest --cov=app --cov-report=xml --cov-fail-under=85 -v

      - name: Upload coverage to Codecov
        uses: codecov/codecov-action@v4
        with:
          file: ./coverage.xml

  docker-build:
    name: Docker build check
    runs-on: ubuntu-latest
    needs: test
    steps:
      - uses: actions/checkout@v4

      - name: Build Docker image
        run: docker build -t payment-gateway:${{ github.sha }} .
```

---

## Interview Cheat Sheet

### "Why did you use a Lua script for the rate limiter?"

> Redis commands from application code are not atomic — between checking the token count and decrementing it, another request can sneak in and both get through. Lua scripts execute atomically on the Redis server as a single unit, so there's no race condition even under high concurrency.

---

### "How does idempotency work in your system?"

> Before processing any charge, we look up the `idempotency_key` in Postgres. If it exists and hasn't expired (24h TTL), we return the stored response verbatim — no DB write, no fraud check, no processor call. This is the same mechanism Stripe uses. It's safe for clients to retry on network failure without fear of double-charging.

---

### "Why sorted sets for fraud detection instead of a counter?"

> A simple counter can't implement a rolling window — it would only count from midnight or the start of a bucket. A Redis sorted set with timestamp as the score lets us do an exact `ZRANGEBYSCORE` query for "any charge in the last 5 minutes", and old entries clean themselves up with `ZREMRANGEBYSCORE`. It's precise, memory-efficient (one entry per charge), and automatically expires.

---

### "What happens if Redis goes down?"

> Currently the rate limiter and fraud engine would fail. In production you'd wrap both in a try/except and fail open (allow the request) rather than fail closed (block everything). You'd also use Redis Sentinel or Redis Cluster for HA. I added a TODO comment in the middleware for this.

---

### "How would you scale this to 10 API instances?"

> The design already supports horizontal scaling: state lives in Postgres (charges, idempotency keys) and Redis (rate limit tokens, fraud velocity counts) — both are external shared stores. The only concern is the idempotency check race condition under concurrent identical requests, which you'd solve with a Postgres `INSERT ... ON CONFLICT DO NOTHING` pattern or a `SELECT FOR UPDATE`.

---

### "Why store amount as integer?"

> Floating-point arithmetic is not associative — `0.1 + 0.2 ≠ 0.3` in IEEE 754. For money, you store the smallest currency unit (pence, cents) as an integer and only convert to decimal for display. This is what every payments company including Stripe does.

---

*End of implementation guide. Build phase by phase, run tests after each one, and commit to Git with meaningful messages. Good luck!*
