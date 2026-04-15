from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app import models, schemas
from app.services.payment_processor import payment_processor

router = APIRouter()

@router.post("/", response_model=schemas.ChargeResponse, status_code=201)
def create_charge(request: schemas.ChargeCreate, db: Session = Depends(get_db)):
    # Simple idempotency check (to be expanded in real scenario)
    existing_charge = db.query(models.Charge).filter_by(
        merchant_id=request.merchant_id,
        idempotency_key=request.idempotency_key
    ).first()
    
    if existing_charge:
        return existing_charge

    # Mock payment processing
    status = payment_processor.process_payment(request.amount, request.currency)

    charge = models.Charge(
        amount=request.amount,
        currency=request.currency.upper(),
        merchant_id=request.merchant_id,
        idempotency_key=request.idempotency_key,
        status=status
    )

    db.add(charge)
    db.commit()
    db.refresh(charge)

    return charge

@router.get("/{charge_id}", response_model=schemas.ChargeResponse)
def get_charge(charge_id: str, db: Session = Depends(get_db)):
    charge = db.query(models.Charge).filter_by(id=charge_id).first()

    if not charge:
        raise HTTPException(status_code=404, detail="Charge not found")
    
    return charge

@router.get("/", response_model=list[schemas.ChargeResponse])
def list_charges(
    merchant_id: str | None = None,
    status: schemas.ChargeStatus | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(models.Charge)

    if merchant_id:
        query = query.filter_by(merchant_id=merchant_id)

    if status:
        query = query.filter_by(status=status)

    return query.order_by(models.Charge.created_at.desc()).all()