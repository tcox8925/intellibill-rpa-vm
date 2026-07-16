from datetime import datetime, timezone
from typing import Optional

from uuid import UUID
from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile, File
from fastapi.security import HTTPBearer
from sqlalchemy import or_
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from app.db.session import get_db
from app.middleware.validator import get_current_user
from app.models import Users, CrmAttachments
from app.schemas import CrmAttachmentUpdate, CrmAttachmentOut
from app.utils.attachment_utility import upload_blob_to_path, delete_blob_from_path
from app.utils.pagination import paginate


router = APIRouter(tags=["AGENT ATTACHMENTS"])
security = HTTPBearer()


@router.get("/agent-attachments", dependencies=[Depends(security)])
def list_agent_attachments(
    npn: Optional[str] = Query(None),
    agent_id: Optional[str] = Query(None), 
    sortColumn: Optional[str] = Query("time_stamp"),
    sortOrder: Optional[str] = Query("desc"),
    page: int = 1,
    page_size: int = 50,
    source_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    query = (
        db.query(CrmAttachments, Users.f_name, Users.l_name, Users.email)
        .join(Users, Users.user_id == CrmAttachments.user_id)
    )

    if npn:
        query = query.filter(CrmAttachments.npn == npn)

    if agent_id:
        query = query.filter(CrmAttachments.agent_id == agent_id)

    if source_id:
        query = query.filter(CrmAttachments.source_id == source_id)

    paginated = paginate(
        query,
        db,
        model=CrmAttachments,
        page=page,
        page_size=page_size,
        sort_column=sortColumn,
        sort_order=sortOrder,
    )

    items = []
    for row in paginated["items"]:
        att_obj, f_name, l_name, email = row
        item_dict = dict(att_obj.__dict__)
        item_dict.pop("_sa_instance_state", None)
        file_name = None
        if item_dict.get("path"):
            try:
                file_name = item_dict["path"].rstrip("/").split("/")[-1]
            except Exception:
                file_name = None
        item_dict["file_name"] = file_name
        item_dict["owner_full_name"] = f"{f_name or ''} {l_name or ''}".strip()
        item_dict["login"] = email
        items.append(item_dict)

    paginated["items"] = items
    return paginated


@router.post("/agent-attachments", response_model=CrmAttachmentOut, dependencies=[Depends(security)])
async def create_agent_attachment(
    agent_id: str = Form(...),
    npn: str = Form(...),
    source_id: Optional[UUID] = Form(None),
    file_type: str = Form(...),
    is_private: bool = Form(False),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        if not file or not file.filename:
            raise HTTPException(status_code=400, detail="No file provided")

        file_content = await file.read()

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        file_ext = file.filename.rsplit(".", 1)
        filename_with_ts = f"{file_ext[0]}_{timestamp}.{file_ext[1]}" if len(file_ext) == 2 else f"{file.filename}_{timestamp}"
        blob_path = f"Documents/{npn}/{filename_with_ts}"

        await upload_blob_to_path(blob_path=blob_path, file=file_content)

        new_att = CrmAttachments(
            path=blob_path,
            file_type=file_type,
            agent_id=agent_id,
            user_id=current_user["user_id"],
            npn=npn,
            time_stamp=datetime.now(timezone.utc),
            source_id=source_id,
            is_private=is_private,
        )
        db.add(new_att)
        db.commit()
        db.refresh(new_att)

        return CrmAttachmentOut(
            pk_id=new_att.pk_id,
            path=new_att.path,
            file_type=new_att.file_type,
            time_stamp=new_att.time_stamp,
            user_id=new_att.user_id,
            agent_id=new_att.agent_id,
            is_private=new_att.is_private,
            owner_full_name=current_user.get("unique_name"),
            login=current_user.get("email"),
            source_id=new_att.source_id,
        )

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/agent-attachments", response_model=CrmAttachmentOut, dependencies=[Depends(security)])
def update_agent_attachment(
    payload: CrmAttachmentUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        current = db.query(CrmAttachments).filter(CrmAttachments.pk_id == str(payload.pk_id)).first()
    except SQLAlchemyError:
        raise HTTPException(status_code=400, detail="Invalid pk_id format")
    if not current:
        raise HTTPException(status_code=404, detail="Attachment not found")
    if str(current.user_id) != str(current_user.get("user_id")) and current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Not authorized to update this attachment")

    if payload.path is not None:
        current.path = payload.path
    if payload.file_type is not None:
        current.file_type = payload.file_type
    current.time_stamp = datetime.now(timezone.utc)

    db.commit()
    db.refresh(current)

    owner = db.query(Users).filter(Users.user_id == current.user_id).first()
    return CrmAttachmentOut(
        pk_id=current.pk_id,
        path=current.path,
        file_type=current.file_type,
        time_stamp=current.time_stamp,
        user_id=current.user_id,
        agent_id=current.agent_id,
        owner_full_name=(f"{owner.f_name or ''} {owner.l_name or ''}".strip() if owner else None),
        login=(owner.email if owner else None),
    )


@router.delete("/agent-attachments/{pk_id}", dependencies=[Depends(security)])
async def delete_agent_attachment(
    pk_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        att = db.query(CrmAttachments).filter(CrmAttachments.pk_id == pk_id).first()
        if not att:
            raise HTTPException(status_code=404, detail="Attachment not found")

        if (
            str(att.user_id) != str(current_user.get("user_id"))
            and current_user.get("role") != "admin"
        ):
            raise HTTPException(status_code=403, detail="Not authorized to delete this attachment")

        # Delete blob from Azure Storage
        await delete_blob_from_path(blob_path=att.path)

        # Delete record from DB
        db.delete(att)
        db.commit()

        return {"detail": "Attachment deleted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

