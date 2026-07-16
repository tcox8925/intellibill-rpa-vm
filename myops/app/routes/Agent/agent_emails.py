from fastapi import APIRouter, Depends, HTTPException, Form, File, UploadFile
from typing import List, Optional, Union
from datetime import datetime, timezone
from requests import Session
from app.db.session import get_db
from app.models.agentModels.email_store import EmailStore, TicketsEmailStore
from app.schemas.Agent import SendAgentEmail
from app.models.Emails.EmailModal import EmailService, EmailContent
from app.core.config import settings
from app.utils.attachment_utility import upload_blob_to_path
import uuid
import logging
import base64
import json
logger = logging.getLogger(__name__)


router = APIRouter(tags=["AGENT EMAIL ROUTES"])

@router.post("/store-and-send-email")
async def store_and_send_email(
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
    attachments: Optional[List[UploadFile]] = File(None),
    source_id: Optional[uuid.UUID] = Form(None),

    db: Session = Depends(get_db)
):
    try:
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
        email = EmailStore(
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
            source_id=source_id
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

        # print("email_details:", {
        #     "sender": sender,
        #     "to": recipients,
        #     "cc": cc,
        #     "bcc": bcc,
        #     "subject": subject,
        #     "body": body,
        #     "attachments": email_attachments,
        #     "body_format": body_format
        # })

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

