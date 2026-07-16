from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, Query,HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import get_db
from app.models.agentModels.crm_tickets_users import CrmTicketsUsers
from app.schemas.CrmTicketsUsers import CrmTicketsUsersCreate,CrmTicketsUsersOut,CrmTicketsUsersUpdate
from app.models.Users import Users
from app.utils.pagination import paginate
from fastapi.security import HTTPBearer
from app.middleware.validator import get_current_user

router = APIRouter(tags=["TICKET USERS ROUTES"])
security = HTTPBearer()

@router.get(
    "/crm-tickets-users",
    response_model=dict,  
)
@router.get(
    "/crm-tickets-users",
    response_model=dict,
)
def list_crm_tickets_users(
    sortColumn: Optional[str] = Query("time_stamp"),
    sortOrder: Optional[str] = Query("desc"),
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
):
    query = (
        db.query(
            CrmTicketsUsers,
            Users.email.label("user_email"),
            Users.f_name.label("first_name"),
            Users.l_name.label("last_name"),
        )
        .join(Users, Users.user_id == CrmTicketsUsers.user_id)
    )

    paginated = paginate(
        query,
        db,
        model=CrmTicketsUsers,
        page=page,
        page_size=page_size,
        sort_column=sortColumn,  
        sort_order=sortOrder,
    )

    paginated["items"] = [
        {
            "pk_id": record.pk_id,
            "user_id": record.user_id,
            "user_email": user_email,
            "time_stamp": record.time_stamp,
            "status": record.status,
            "first_name": first_name,
            "last_name": last_name,
        }
        
        for record, user_email, first_name, last_name in paginated["items"]
    ]

    return paginated


@router.post(
    "/crm-tickets-users",
    response_model=CrmTicketsUsersOut,
    dependencies=[Depends(security)],
)
def create_or_update_crm_tickets_user(
    payload: CrmTicketsUsersCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Not authorized to create/update this record")

    # Check if user_id already exists
    record = (
        db.query(CrmTicketsUsers)
        .filter(CrmTicketsUsers.user_id == payload.user_id)
        .first()
    )

    now = datetime.now(timezone.utc)

    if record:
        # UPDATE existing row
        record.status = payload.status
        record.time_stamp = now
    else:
        # CREATE new row
        record = CrmTicketsUsers(
            user_id=payload.user_id,
            status=payload.status,
            time_stamp=now,
        )
        db.add(record)

    db.flush()
    db.refresh(record)

    user = (
        db.query(Users)
        .filter(Users.user_id == record.user_id)
        .first()
    )

    return {
        "pk_id": record.pk_id,
        "status": record.status,
        "user_id": record.user_id,
        "user_email": user.email if user else None,
        "time_stamp": record.time_stamp.isoformat() if record.time_stamp else None,
        "first_name": user.f_name if user else None,
        "last_name": user.l_name if user else None,
    }
  
  
@router.patch(
    "/crm-tickets-users",
    response_model=CrmTicketsUsersOut,
    dependencies=[Depends(security)],
)
def update_crm_tickets_user(
    payload: CrmTicketsUsersUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        current = (
            db.query(CrmTicketsUsers)
            .filter(CrmTicketsUsers.user_id == payload.user_id)
            .first()
        )
    except SQLAlchemyError:
        raise HTTPException(status_code=400, detail="Invalid user_id format")

    if not current:
        raise HTTPException(status_code=404, detail="Record not found")

    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Not authorized to create/update this record")

    if payload.status is not None:
        current.status = payload.status

    db.flush()
    db.refresh(current)

    user = (
        db.query(Users)
        .filter(Users.user_id == current.user_id)
        .first()
    )

    return {
        "pk_id": current.pk_id,
        "user_id": current.user_id,
        "status":current.status,
        "user_email": user.email if user else None,
        "time_stamp": current.time_stamp.isoformat() if current.time_stamp else None,
        "first_name": user.f_name if user else None,
        "last_name": user.l_name if user else None,
    }
    

@router.delete(
    "/crm-tickets-users",
    dependencies=[Depends(security)],
)
def delete_crm_tickets_user(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        record = (
            db.query(CrmTicketsUsers)
            .filter(CrmTicketsUsers.user_id == user_id)
            .first()
        )
    except SQLAlchemyError:
        raise HTTPException(status_code=400, detail="Invalid user_id format")

    if not record:
        raise HTTPException(status_code=404, detail="Record not found")

    # Authorization: same user or admin
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Not authorized to create/update this record")

    db.delete(record)
    db.flush()

    return {"detail": "CRM ticket user deleted successfully"}
    
