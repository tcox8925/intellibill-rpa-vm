from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from typing import List, Optional
from sqlalchemy import Integer, cast, func, or_
from app.middleware.validator import get_current_user
from app.models import CrmTicket, Users
# from app.models.AzureGraphModel import get_user_inbox
from app.models.Emails.EmailModal import EmailContent, EmailService
from app.models.agentModels.email_store import TicketsEmailStore
from app.models.agentModels.tickets_audit_history import TicketsAuditHistory
from app.schemas.Agent import TicketSchema, UpdateTicketSchema, TicketAuditCreate
from sqlalchemy.orm import Session
from app.db.session import get_db
import uuid, json
import logging
import base64
from datetime import datetime, timezone



from app.utils.attachment_utility import upload_blob_to_path
logger = logging.getLogger(__name__)

router = APIRouter(tags=["TICKET ROUTES"])


@router.get("/tickets/crm-tickets/filters")
def get_ticket_filters(db: Session = Depends(get_db)):
    subject_rows = (
        db.query(CrmTicket.subject)
        .filter(CrmTicket.subject.isnot(None))
        .distinct()
        .order_by(CrmTicket.subject.asc())
        .all()
    )
    status_rows = (
        db.query(CrmTicket.status)
        .filter(CrmTicket.status.isnot(None))
        .distinct()
        .order_by(CrmTicket.status.asc())
        .all()
    )
    owner_rows = (
        db.query(CrmTicket.owner, Users.f_name, Users.l_name)
        .outerjoin(Users, CrmTicket.owner == Users.email)
        .filter(CrmTicket.owner.isnot(None))
        .distinct()
        .order_by(CrmTicket.owner.asc())
        .all()
    )

    owners = []
    for email, f_name, l_name in owner_rows:
        if not email:
            continue
        full_name = f"{f_name or ''} {l_name or ''}".strip()
        owners.append({
            "email": email,
            "name": full_name or email
        })

    return {
        "subjects": [row[0] for row in subject_rows if row[0]],
        "statuses": [row[0] for row in status_rows if row[0]],
        "owners": owners,
    }

@router.get("/tickets/crm-tickets")
def get_crm_tickets(
    owner_email: Optional[List[str]] = Query(None, description="Filter by ticket owner email"),
    agent_email: Optional[str] = Query(None, description="Filter by agent email"),
    page: int = Query(1, ge=1, description="Page number (starting from 1)"),
    page_size: int = Query(10, ge=1, le=100, description="Number of records per page"),
    type: Optional[str] = Query(None, description="Filter by ticket type"),
    status: Optional[List[str]] = Query(None, description="Filter by ticket status"),
    subject: Optional[List[str]] = Query(None, description="Filter by ticket subject (contains search; multi-select supported)"),
    entity_id: Optional[str] = Query(None, description="Filter by entity ID"),
    sub_entity_id: Optional[str] = Query(None, description="Filter by sub-entity ID"),
    sort_column: Optional[str] = Query(None, description="Column to sort by (ticket_id, created_at, subject, status, type)"),
    sort_order: Optional[str] = Query("desc", description="Sort direction: asc or desc"),
    db: Session = Depends(get_db)
):

    query = (
        db.query(
            CrmTicket,
            Users.f_name,
            Users.l_name,
            Users.role,
            Users.email
        )
        .outerjoin(Users, CrmTicket.owner == Users.email)
    )

    owner_values = [value for value in (owner_email or []) if value]
    if owner_values:
        query = query.filter(CrmTicket.owner.in_(owner_values))

    if agent_email:
        query = query.filter(CrmTicket.agent_email == agent_email)

    if type:
        query = query.filter(CrmTicket.type == type)

    status_values = [value for value in (status or []) if value]
    if status_values:
        query = query.filter(CrmTicket.status.in_(status_values))

    subject_values = [value for value in (subject or []) if value]
    if subject_values:
        ilike_filters = [CrmTicket.subject.ilike(f"%{value}%") for value in subject_values]
        query = query.filter(or_(*ilike_filters))

    if entity_id:
        query = query.filter(CrmTicket.entity_id == entity_id)

    if sub_entity_id:
        query = query.filter(CrmTicket.sub_entity_id == sub_entity_id)

    total_count = query.count()

    sort_field = (sort_column or "created_at").lower()
    sort_direction = (sort_order or "desc").lower()
    if sort_direction not in {"asc", "desc"}:
        sort_direction = "desc"

    order_expressions = []

    if sort_field == "ticket_id":
        ticket_number = cast(func.split_part(CrmTicket.ticket_id, "-", 2), Integer)
        order_expressions.append(
            ticket_number.desc() if sort_direction == "desc" else ticket_number.asc()
        )
    elif sort_field == "owner_name":
        if sort_direction == "desc":
            order_expressions.extend([Users.f_name.desc(), Users.l_name.desc()])
        else:
            order_expressions.extend([Users.f_name.asc(), Users.l_name.asc()])
    else:
        sort_attr = getattr(CrmTicket, sort_field, None)
        if not sort_attr:
            raise HTTPException(status_code=400, detail=f"Unsupported sort column: {sort_field}")
        order_expressions.append(
            sort_attr.desc() if sort_direction == "desc" else sort_attr.asc()
        )


    query = query.order_by(*order_expressions)

    tickets = (
        query
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    results = []
    for ticket, f_name, l_name, role, email in tickets:
        attachments = ticket.attachment
        if isinstance(attachments, str):
            try:
                attachments = json.loads(attachments)
            except Exception:
                attachments = []
        if not isinstance(attachments, list):
            attachments = []
        for idx, attachment in enumerate(attachments):
            if isinstance(attachment, dict) and "attachment_id" not in attachment:
                attachment["attachment_id"] = idx + 1
        
        results.append({
            "ticket_id": ticket.ticket_id,
            "subject": ticket.subject,
            "description": ticket.description,
            "type": ticket.type,
            "status": ticket.status,
            "resolution": ticket.resolution,
            "pk_id": ticket.pk_id,
            "created_at": ticket.created_at,
            "created_by": ticket.created_by,
            "owner_name": f"{f_name or ''} {l_name or ''}".strip(),
            "owner_email": ticket.owner,
            "attachments": attachments,
            "agent_email": ticket.agent_email,
            "last_updated": ticket.last_updated,
        })

    return {
        "page": page,
        "page_size": page_size,
        "total_count": total_count,
        "tickets": results
    }


@router.post("/tickets/crm-ticket")
async def update_create_crm_ticket(
    ticket_info: str = Form(...),
    attachments: Optional[List[UploadFile]] = None,
    db: Session = Depends(get_db)
):
    try:
        ticket_data = json.loads(ticket_info)
        ticket = TicketSchema(**ticket_data)

        ticket_count = db.query(func.count(CrmTicket.pk_id)).scalar() or 0
        new_ticket_id = f"TCK-{ticket_count + 1}"

        attachments_json = []

        if attachments:
            attachment_id = 1
            for file in attachments:
                blob_path = f"tickets/{new_ticket_id}/{file.filename}"
                data = await file.read()
                await upload_blob_to_path(blob_path, data)

                attachments_json.append({
                    "attachment_id": attachment_id,
                    "file_name": file.filename,
                    "path": blob_path,
                    "uploaded_at": datetime.utcnow().isoformat()
                })
                attachment_id += 1

        new_ticket = CrmTicket(
            ticket_id=new_ticket_id,
            subject=ticket.subject,
            description=ticket.description,
            type=ticket.type,
            status="Open",
            created_by=ticket.created_by,
            owner=ticket.owner,
            created_at=datetime.utcnow(),
            attachment=attachments_json or None,
            entity_id=ticket.entity_id,
            sub_entity_id=ticket.sub_entity_id,
            resolution=ticket.resolution,
            agent_email=ticket.agent_email,
            last_updated=datetime.utcnow()
        )


        db.add(new_ticket)
        db.commit()
        db.refresh(new_ticket)
        return {
            "message": "Ticket created successfully",
            "ticket_id": new_ticket.ticket_id,
            "entity_id": new_ticket.entity_id,
            "sub_entity_id": new_ticket.sub_entity_id,
            "attachment": attachments_json
        }

    except Exception as e:
        logger.exception("Failed to create ticket", exc_info=e)
        raise HTTPException(500, "Failed to create ticket")


@router.patch("/tickets/crm-ticket")
async def update_crm_ticket(
    ticket_info: str = Form(...),
    attachments: Optional[List[UploadFile]] = None,
    db: Session = Depends(get_db),
):
    try:
        item = json.loads(ticket_info)
    except Exception:
        raise HTTPException(status_code=400, detail="ticket_info must be a valid JSON object.")
    if not isinstance(item, dict):
        raise HTTPException(status_code=400, detail="ticket_info must be a JSON object.")
    try:
        update_data = UpdateTicketSchema(**item)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Validation error: {e}")

    ticket = db.query(CrmTicket).filter(CrmTicket.ticket_id == update_data.ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail=f"Ticket not found: {update_data.ticket_id}")
    for field in ["subject", "description", "type", "status", "owner", "resolution", "entity_id", "sub_entity_id"]:
        value = getattr(update_data, field, None)
        if value is not None:
            old_value = getattr(ticket, field, None)
            setattr(ticket, field, value)

    if update_data.agent_email:
        ticket.agent_email = update_data.agent_email

    ticket.last_updated = datetime.utcnow()

    current_attachments = ticket.attachment
    if isinstance(current_attachments, str):
        try:
            current_attachments = json.loads(current_attachments)
        except Exception:
            current_attachments = []

    if not isinstance(current_attachments, list):
        current_attachments = []
    max_attachment_id = 0
    for att in current_attachments:
        if isinstance(att, dict) and "attachment_id" in att:
            max_attachment_id = max(max_attachment_id, att["attachment_id"])

    new_attachments_list = []
    if attachments:
        attachment_id = max_attachment_id + 1
        for file in attachments:
            if file.filename:
                blob_path = f"tickets/{ticket.ticket_id}/{file.filename}"

                data = await file.read()
                await upload_blob_to_path(blob_path, data)

                file_meta = {
                    "attachment_id": attachment_id,
                    "file_name": file.filename,
                    "path": blob_path,
                    "uploaded_at": datetime.utcnow().isoformat()
                }

                new_attachments_list.append(file_meta)
                attachment_id += 1

        ticket.attachment = current_attachments + new_attachments_list

    db.add(ticket)
    db.commit()

    return {
        "message": "Ticket updated successfully",
        "ticket_id": ticket.ticket_id,
        "new_attachment": new_attachments_list
    }
    
@router.get("/tickets/crm-tickets/{ticket_id}")
def get_crm_ticket_by_id(
    ticket_id: str,
    entity_id: Optional[str] = Query(None, description="Filter by entity ID"),
    sub_entity_id: Optional[str] = Query(None, description="Filter by sub-entity ID"),
    db: Session = Depends(get_db)
):
    query = db.query(CrmTicket).filter(CrmTicket.ticket_id == ticket_id)

    if entity_id:
        query = query.filter(CrmTicket.entity_id == entity_id)

    if sub_entity_id:
        query = query.filter(CrmTicket.sub_entity_id == sub_entity_id)

    ticket = query.first()

    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    return ticket

@router.delete("/tickets/crm-tickets/{ticket_id}/attachments/{attachment_id}")
def delete_ticket_attachment(
    ticket_id: str,
    attachment_id: int,
    db: Session = Depends(get_db)
):
    """
    Delete a specific attachment from a ticket by attachment_id
    """
    try:
        ticket = db.query(CrmTicket).filter(CrmTicket.ticket_id == ticket_id).first()
        
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")
        
        current_attachments = ticket.attachment
        if isinstance(current_attachments, str):
            try:
                current_attachments = json.loads(current_attachments)
            except Exception:
                current_attachments = []
        
        if not isinstance(current_attachments, list):
            current_attachments = []
        
        if not current_attachments:
            raise HTTPException(status_code=404, detail="No attachments found for this ticket")
        for idx, attachment in enumerate(current_attachments):
            if isinstance(attachment, dict) and "attachment_id" not in attachment:
                attachment["attachment_id"] = idx + 1

        attachment_found = False
        deleted_file_name = None
        updated_attachments = []
        
        for attachment in current_attachments:
            if attachment.get("attachment_id") == attachment_id:
                attachment_found = True
                deleted_file_name = attachment.get("file_name")
                continue
            updated_attachments.append(attachment)
        
        if not attachment_found:
            raise HTTPException(status_code=404, detail=f"Attachment with ID {attachment_id} not found")
        
        ticket.attachment = updated_attachments if updated_attachments else None
        
        db.commit()
        
        return {
            "message": "Attachment deleted successfully",
            "ticket_id": ticket_id,
            "attachment_id": attachment_id,
            "deleted_file": deleted_file_name,
            "remaining_attachments": len(updated_attachments)
        }
    
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.exception("Failed to delete attachment", exc_info=e)
        raise HTTPException(status_code=500, detail=f"Failed to delete attachment: {str(e)}")
    
@router.post("/tickets/email")
async def store_and_send_email_tickets(
    sender: str = Form(...),
    recipients: List[str] = Form(...),
    cc: Optional[List[str]] = Form(None),
    bcc: Optional[List[str]] = Form(None),
    subject: Optional[str] = Form(None),
    body: str = Form(...),
    body_format: str = Form("plain"),
    email_type: str = Form(...),  # mail/draft/scheduled
    sent_datetime: Optional[str] = Form(None),
    schedule_datetime: Optional[str] = Form(None),
    ticket_id: str = Form(...),
    attachments: Optional[List[UploadFile]] = File(None),

    db: Session = Depends(get_db)
):
    try:
        try:
            ticket_ref = uuid.UUID(ticket_id) if isinstance(ticket_id, str) else ticket_id
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="ticket_id must be a valid UUID")
        def parse_date(dt):
            return datetime.fromisoformat(dt) if dt else None
        
        def normalize_emails(value):
            if not value:
                return []
            # if single comma-separated string
            if isinstance(value, str):
                value = value.split(",")
            # if already a list, flatten any comma-separated items within
            elif isinstance(value, list):
                cleaned = []
                for v in value:
                    cleaned.extend(v.split(","))
                value = cleaned
            return [v.strip() for v in value if v and v.strip()]
        
        recipients = normalize_emails(recipients)
        cc = normalize_emails(cc)
        bcc = normalize_emails(bcc)

        # ---- STEP 1: Create record first with status="sending" ----
        email = TicketsEmailStore(
            sender=sender,
            recipients=recipients,
            cc=cc,
            bcc=bcc,
            subject=subject,
            body=body,
            body_format=body_format,
            email_type=email_type,
            sent_datetime=parse_date(sent_datetime),
            schedule_datetime=parse_date(schedule_datetime),
            status="sending",
            ticket_id=ticket_ref
        )

        db.add(email)
        db.commit()
        db.refresh(email)

        MAX_ATTACHMENT_SIZE = 7 * 1024 * 1024  # 7 MB

        pk_id = email.pk_id
        attachment_records = []
        total_size = 0
        email_attachments = []  # <--- store file bytes for sending

        if attachments:
            for file in attachments:
                file_bytes = await file.read()
                file_size = len(file_bytes)
                total_size += file_size

                # Check size limit (total across all attachments)
                if total_size > MAX_ATTACHMENT_SIZE:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Total attachment size exceeds 20MB limit"
                    )

                blob_folder = f"emails/{pk_id}"
                blob_path = f"{blob_folder}/{file.filename}"

                meta = await upload_blob_to_path(blob_path, file_bytes, overwrite=True)

                # store DB metadata
                attachment_records.append({
                    "id": str(uuid.uuid4()),
                    "file_name": file.filename,
                    "blob_path": blob_path,
                    "mime_type": file.content_type,
                    "size": len(file_bytes)
                })

                # store actual file bytes for sending email
                email_attachments.append({
                    "file_name": file.filename,
                    "content": base64.b64encode(file_bytes).decode(),
                    "content_type": file.content_type
                })

        # Save attachments JSON
        if attachment_records:
            email.attachments = attachment_records
            db.commit()
            db.refresh(email)

        if email_type == "draft":
            return {"success": True, "email_id": pk_id}

        if email_type == "scheduled" and schedule_datetime:
            email.status = "scheduled"
            db.commit()
            db.refresh(email)
            return {"success": True, "email_id": pk_id}

        # ---- STEP 3: SEND THE EMAIL ----
        email_service = EmailService()
        email_model = EmailContent(
            sender=sender,
            to=recipients,
            cc=cc,
            bcc=bcc,
            subject=subject,
            body=body,
            html=(body_format == "html"),
            attachments=email_attachments
        )

        send_result = await email_service.send_email(email_model)

        # ---- STEP 4: Update status ----
        if send_result.get("status") == "Succeeded":
            email.status = "sent"

            # Set actual sent_datetime
            email.sent_datetime = datetime.now(timezone.utc)

        else:
            email.status = "failed"

        db.commit()
        db.refresh(email)

        return {
            "success": True,
            "email_id": pk_id,
            "status": email.status,
            "attachments": attachment_records,
            "email_response": send_result
        }

    except Exception as e:
        logger.exception("Error storing/sending email")

        # mark DB record failed if exception happened AFTER creating it
        try:
            email.status = "failed"
            db.commit()
        except:
            pass

        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tickets/email/{ticket_id}")
async def get_ticket_emails(
    ticket_id: uuid.UUID,
    db: Session = Depends(get_db)
):
    try:
        emails = (
            db.query(TicketsEmailStore)
            .filter(TicketsEmailStore.ticket_id == ticket_id)
            .order_by(
                TicketsEmailStore.sent_datetime.desc(),
                TicketsEmailStore.schedule_datetime.desc(),
                TicketsEmailStore.pk_id.desc(),
            )
            .all()
        )

        def deserialize_email_list(value):
            """Convert email list from various formats to proper list"""
            if not value:
                return []
            if isinstance(value, list):
                return value
            if isinstance(value, str):
                cleaned = value.strip("{}[]")
                if cleaned:
                    return [e.strip() for e in cleaned.split(",") if e.strip()]
            return []
        
        results = []
        for email in emails:
            results.append(
                {
                    "pk_id": str(email.pk_id),
                    "sender": email.sender,
                    "recipients": deserialize_email_list(email.recipients),
                    "cc": deserialize_email_list(email.cc),
                    "bcc": deserialize_email_list(email.bcc),
                    "subject": email.subject,
                    "body": email.body,
                    "body_format": email.body_format,
                    "email_type": email.email_type,
                    "sent_datetime": email.sent_datetime,
                    "schedule_datetime": email.schedule_datetime,
                    "attachments": email.attachments,
                    "status": email.status,
                    "ticket_id": str(email.ticket_id),
                }
            )

        return {"count": len(results), "emails": results}

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to fetch ticket email history", exc_info=exc)
        raise HTTPException(status_code=500, detail="Failed to fetch ticket emails")
    
# @router.get("/tickets/threads")
# async def get_ticket_threads():
#     USER_EMAIL = "sibtain@834labs.com"
#     all_emails = await get_user_inbox(USER_EMAIL, top=5, unread_only=False)
#     print("test", all_emails)
#     return {"emails": all_emails}


@router.post("/tickets/crm-tickets/{ticket_id}/audit-history")
def create_ticket_audit_entry(
    ticket_id: str,
    audit_data: TicketAuditCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    ticket = db.query(CrmTicket).filter(CrmTicket.ticket_id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    try:
        audit_entry = TicketsAuditHistory(
            ticket_id=ticket.pk_id,
            user_id=current_user.get("user_id"),    
            action=audit_data.action,
            action_message=audit_data.action_message,
            tab=audit_data.tab,
            entity_id=audit_data.entity_id,
            sub_entity_id=audit_data.sub_entity_id
        )
        db.add(audit_entry)
        db.commit()
        db.refresh(audit_entry)
        
        return {
            "success": True,
            "audit_id": str(audit_entry.pk_id),
            "ticket_id": ticket_id,
            "action": audit_entry.action,
            "created_at": audit_entry.created_at
        }
    except Exception as e:
        db.rollback()
        logger.exception("Failed to create audit entry", exc_info=e)
        raise HTTPException(status_code=500, detail="Failed to create audit entry")

@router.get("/tickets/crm-tickets/{ticket_id}/audit-history")
def get_ticket_audit_history(
    ticket_id: str,
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=100, description="Records per page"),
    action: Optional[str] = Query(None, description="Filter by action (CREATE, UPDATE, DELETE, etc.)"),
    db: Session = Depends(get_db)
):
    ticket = db.query(CrmTicket).filter(CrmTicket.ticket_id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    query = db.query(
        TicketsAuditHistory,
        Users.f_name,
        Users.l_name,
        Users.email
    ).outerjoin(
        Users, Users.user_id == TicketsAuditHistory.user_id
    ).filter(
        TicketsAuditHistory.ticket_id == ticket.pk_id
    )
    
    if action:
        query = query.filter(TicketsAuditHistory.action == action)
    query = query.order_by(TicketsAuditHistory.created_at.desc())
    total = query.count()
    offset = (page - 1) * page_size
    results = query.offset(offset).limit(page_size).all()
    
    items = [
        {
            "pk_id": str(audit.pk_id),
            "ticket_id": ticket_id,
            "action": audit.action,
            "action_message": audit.action_message,
            "user_id": str(audit.user_id),
            "user_name": f"{f_name or ''} {l_name or ''}".strip() if f_name or l_name else None,
            "user_email": email,
            "tab": audit.tab,
            "entity_id": audit.entity_id,
            "sub_entity_id": audit.sub_entity_id,
            "created_at": audit.created_at
        }
        for audit, f_name, l_name, email in results
    ]
    
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items
    }
