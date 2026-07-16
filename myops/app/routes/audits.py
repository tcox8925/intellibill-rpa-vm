from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.security import HTTPBearer
from app.models import CRMAuditHistory
from sqlalchemy.orm import Session
from sqlalchemy import and_, func
from app.db.session import get_db
from app.schemas.Agent import CrmAuditHistoryCreate
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime
from typing import Optional

router = APIRouter(tags=["AUDIT ROUTES"])
security = HTTPBearer()


@router.post("/audit-history", summary="Track user dashboard actions", dependencies=[Depends(security)])
def track_user_action(
    audit_data: CrmAuditHistoryCreate,
    db: Session = Depends(get_db)
):

    try:
        audit_entry = CRMAuditHistory(**audit_data.dict())

        db.add(audit_entry)
        db.commit()
        db.refresh(audit_entry)

        return {
            "message": "User action tracked successfully",
            "audit_id": audit_entry.audit_id,
            "action": audit_entry.action,
            "user": audit_entry.login_user,
            "timestamp": audit_entry.created_at
        }

    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to track user action"
        )


@router.get("/audit-history", summary="Get audit history for an agent", dependencies=[Depends(security)])
def get_audit_history(
    agent_id: Optional[str] = Query(None),
    sub_module: Optional[str] = Query(None),
    login_user: Optional[str] = Query(None),
    created_at: Optional[datetime] = Query(None),
    source_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=1000),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("desc"),
    db: Session = Depends(get_db)
):
    
    valid_sort_fields = ["created_at", "action", "login_user", "agent_id", "sub_module"]
    if sort_by not in valid_sort_fields:
        sort_by = "created_at"
    
    if sort_order.lower() not in ["asc", "desc"]:
        sort_order = "desc"
    
    filters = []
    
    if agent_id:
        filters.append(CRMAuditHistory.agent_id == agent_id)
    
    if sub_module:
        filters.append(CRMAuditHistory.sub_module.ilike(f"%{sub_module}%"))
    
    if login_user:
        filters.append(CRMAuditHistory.login_user.ilike(f"%{login_user}%"))
    
    if created_at:
        filters.append(CRMAuditHistory.created_at == created_at)

    if source_id:
        filters.append(CRMAuditHistory.source_id == source_id)
    
    query = db.query(CRMAuditHistory)
    
    if filters:
        query = query.filter(and_(*filters))

    total = query.count()
    offset = (page - 1) * page_size
    
    sort_column = getattr(CRMAuditHistory, sort_by)
    if sort_order.lower() == "asc":
        query = query.order_by(sort_column.asc())
    else:
        query = query.order_by(sort_column.desc())
    
    audit_entries = query.offset(offset)\
        .limit(page_size)\
        .all()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": audit_entries
    }


@router.get("/audit-history/filters", dependencies=[Depends(security)])
def get_filter_values(
    agent_id: Optional[str] = Query(None, description="Agent ID (optional)"),
    db: Session = Depends(get_db)
):
    query_base = db.query(CRMAuditHistory)
    
    if agent_id:
        query_base = query_base.filter(CRMAuditHistory.agent_id == agent_id)
    
    sub_modules = query_base.with_entities(CRMAuditHistory.sub_module)\
        .distinct()\
        .filter(CRMAuditHistory.sub_module.isnot(None))\
        .order_by(CRMAuditHistory.sub_module)\
        .all()
    
    login_users = query_base.with_entities(CRMAuditHistory.login_user)\
        .distinct()\
        .filter(CRMAuditHistory.login_user.isnot(None))\
        .order_by(CRMAuditHistory.login_user)\
        .all()
    
    created_dates = query_base.with_entities(CRMAuditHistory.created_at)\
        .distinct()\
        .filter(CRMAuditHistory.created_at.isnot(None))\
        .order_by(CRMAuditHistory.created_at.desc())\
        .all()
    
    return {
        "sub_modules": [item[0] for item in sub_modules],
        "login_users": [item[0] for item in login_users],
        "created_dates": [str(item[0]) for item in created_dates if item[0]]
    }


