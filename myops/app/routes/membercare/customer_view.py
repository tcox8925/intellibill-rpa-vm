from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import cast, String

from app.db.session import get_db
from app.models.membercare.MembercareCustomer import MembercareCustomer
from app.schemas.Agent import NameValueModel
from app.schemas.membercare_customers import (
    MembercareCustomerResponse,
    MembercareCustomerCreateSchema,
    MembercareCustomerUpdateSchema,
)

router = APIRouter(tags=["MEMBERCARE CUSTOMERS VIEW"])

MC = MembercareCustomer


@router.get("/membercare-customers/list", response_model=list[NameValueModel])
def get_membercare_customers_list(
    search: str | None = Query(None, description="Search by caller name"),
    db: Session = Depends(get_db),
):
    query = db.query(MC)
    if search:
        pattern = f"%{search}%"
        query = query.filter(MC.caller_name.ilike(pattern))
    query = query.order_by(MC.caller_name.asc()).limit(50)
    return [{"value": str(c.id), "name": c.caller_name} for c in query.all()]


@router.get("/membercare-customers/{customer_id}")
def get_membercare_customer(
    customer_id: str,
    db: Session = Depends(get_db),
):
    customer = db.query(MC).filter(cast(MC.id, String) == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    return MembercareCustomerResponse.model_validate(customer)


@router.post("/membercare-customers", status_code=201)
def create_membercare_customer(
    payload: MembercareCustomerCreateSchema,
    db: Session = Depends(get_db),
):
    try:
        customer_data = payload.model_dump()
        new_customer = MC(**customer_data)
        db.add(new_customer)
        db.commit()
        db.refresh(new_customer)

        return {"message": "Membercare customer created successfully", "id": str(new_customer.id)}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/membercare-customers/{customer_id}")
def update_membercare_customer(
    customer_id: str,
    payload: MembercareCustomerUpdateSchema,
    db: Session = Depends(get_db),
):
    customer = db.query(MC).filter(cast(MC.id, String) == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    try:
        updates = payload.model_dump(exclude_unset=True)
        for key, value in updates.items():
            setattr(customer, key, value)

        db.commit()
        return {"message": "Membercare customer updated successfully"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
