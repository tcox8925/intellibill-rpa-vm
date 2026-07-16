import datetime
import json
from typing import Literal, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from requests import Session
from app.core.helpers import CENTRAL_TZ, to_central_time
from app.db.session import get_db
from app.middleware.validator import get_current_user
from app.schemas.Agent import SendAgentEmail, TemplateSchema, OrganizationEmailCreateSchema, OrganizationEmailUpdateSchema, OrganizationEmailResponseSchema
from app.models.agentModels.email_store import EmailStore
from app.models.agentModels.email_template import EmailTemplate
from app.models.Emails.OrganizationEmails import OrganizationEmails
from app.models.Users import Users
import re
from app.core.config import settings
import logging
logger = logging.getLogger(__name__)
router = APIRouter(tags=["EMAIL ROUTES"])

@router.get("/emails/organization-emails")
async def get_organization_emails(db: Session = Depends(get_db)):
    try:
        query = db.query(OrganizationEmails)
        return query.all()
    except Exception as e:
        logger.exception(str(e))
        raise HTTPException(status_code=500, detail=str("Error fetching organization emails"))
    
@router.post(
    "/emails/organization-emails",
    response_model=OrganizationEmailResponseSchema
)
async def create_organization_email(
    payload: OrganizationEmailCreateSchema,
    db: Session = Depends(get_db)
):
    try:
        record = OrganizationEmails(**payload.dict())
        db.add(record)
        db.commit()
        db.refresh(record)
        return record
    except Exception as e:
        db.rollback()
        logger.exception(str(e))
        raise HTTPException(status_code=500, detail="Error creating organization email")

@router.patch(
    "/emails/organization-emails/{pk_id}",
    response_model=OrganizationEmailResponseSchema
)
async def update_organization_email(
    pk_id: UUID,
    payload: OrganizationEmailUpdateSchema,
    db: Session = Depends(get_db)
):
    try:
        record = db.query(OrganizationEmails).filter(
            OrganizationEmails.pk_id == pk_id
        ).first()

        if not record:
            raise HTTPException(status_code=404, detail="Email record not found")

        for key, value in payload.dict(exclude_unset=True).items():
            setattr(record, key, value)

        db.commit()
        db.refresh(record)
        return record

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.exception(str(e))
        raise HTTPException(status_code=500, detail="Error updating organization email")

@router.delete(
    "/emails/organization-emails/{pk_id}"
)
async def delete_organization_email(
    pk_id: UUID,
    db: Session = Depends(get_db)
):
    try:
        record = db.query(OrganizationEmails).filter(
            OrganizationEmails.pk_id == pk_id
        ).first()

        if not record:
            raise HTTPException(status_code=404, detail="Email record not found")

        db.delete(record)
        db.commit()
        return "Data deleted successfully"

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.exception(str(e))
        raise HTTPException(status_code=500, detail="Error deleting organization email")

@router.post("/emails")
def create_agent_email(
    email_data: SendAgentEmail,
    db: Session = Depends(get_db)
):
    try:
        email_dict = email_data.dict(exclude={"sent_datetime"})
        email_dict["sent_datetime"] = datetime.now(CENTRAL_TZ)
        if email_data.email_type == "mail":
            email_dict["status"] = "sent"

        elif email_data.email_type == "scheduled":
            if not email_data.schedule_datetime:
                raise HTTPException(
                    status_code=400,
                    detail="schedule_datetime is required for scheduled emails"
                )
            email_dict["schedule_datetime"] = to_central_time(
                email_data.schedule_datetime
            )
            email_dict["status"] = "scheduled"

        elif email_data.email_type == "draft":
            email_dict["status"] = "draft"

        email_entry = EmailStore(**email_dict)
        db.add(email_entry)
        db.commit()
        db.refresh(email_entry)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if email_data.email_type == "draft":
        message = "Email saved as draft"
    elif email_data.email_type == 'scheduled':
        message = "Email scheduled for",email_data.schedule_datetime
    else:
        message = "Email sent successfully"
    return {
        "message": message
    }

@router.get("/emails")
def fetch_agent_emails(
    email_type: Literal["mail", "draft", "scheduled", "failed"],
    email_id: str = Query(None),
    source_id: Optional[UUID] = Query(None),
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db)
):
    
    if email_type == "failed":
        emails = (
            db.query(EmailStore)
            .filter(
                EmailStore.status == 'failed'
            )
        )
        if email_id:
            emails = emails.filter(EmailStore.sender == email_id)
    else:
        emails = (
            db.query(EmailStore)
            .filter(
                EmailStore.email_type == email_type
            )
        )
        if email_id:
            emails = emails.filter(EmailStore.sender == email_id)

    if source_id:
        emails = emails.filter(EmailStore.source_id == source_id)

    emails = emails.order_by(EmailStore.sent_datetime.desc()) \
        .offset((page - 1) * page_size) \
        .limit(page_size) \
        .all()
    
    email_count = (
        db.query(EmailStore)
        .filter(
            EmailStore.email_type == email_type,
        )
    )
    if email_id:
        email_count = email_count.filter(EmailStore.sender == email_id)

    if source_id:
        email_count = email_count.filter(EmailStore.source_id == source_id)

    email_count = email_count.count()

    result = {
        "page": page,
        "page_size": page_size,
        "total": email_count,
        "emails": []
    }
    for email in emails:
        email_dict = email.__dict__.copy()
        email_dict.pop("_sa_instance_state", None)

        # Clean recipients, cc, bcc
        for field in ["recipients", "cc", "bcc"]:
            val = email_dict.get(field)
            if val and isinstance(val, str):
                cleaned = val.strip("{}").strip()

                # Remove leading/trailing quotes or commas
                cleaned = re.sub(r'^[,"\']+|[,"\']+$', '', cleaned).strip()

                # Replace escaped quotes or stray backslashes
                cleaned = cleaned.replace('\\"', '').replace("\\", "").strip()

                # Clean repeated commas/spaces
                cleaned = re.sub(r'\s*,\s*', ', ', cleaned)

                # Final cleanup: if empty, set None
                email_dict[field] = cleaned if cleaned else None

        
        result["emails"].append(email_dict)

    return result


@router.get("/fetch-all-templates")
def fetch_all_templates(
    entity_id: Optional[str] = Query(None),
    sub_entity_id: Optional[str] = Query(None),
    module: Optional[str] = Query(None),
    db: Session = Depends(get_db)):
    query = db.query(
        EmailTemplate,
        Users.f_name,
        Users.l_name,
        Users.email,
        Users.login
    ).outerjoin(
        Users, EmailTemplate.owner == Users.user_id
    )
    
    if entity_id:
        query = query.filter(EmailTemplate.entity_id == entity_id)
    if sub_entity_id:
        query = query.filter(EmailTemplate.sub_entity_id == sub_entity_id)
    if module:
        query = query.filter(EmailTemplate.template_module == module)
    
    results = query.all()
    
    templates = []
    for template, f_name, l_name, email, login in results:
        template_dict = {
            "template_id": template.template_id,
            "template": template.template,
            "template_name": template.template_name,
            "template_category": template.template_category,
            "template_module": template.template_module,
            "template_design": template.template_design,
            "entity_id": template.entity_id,
            "sub_entity_id": template.sub_entity_id,
            "full_name": f"{f_name or ''} {l_name or ''}".strip() or None,
            "email": email,
            "login": login
        }
        templates.append(template_dict)
    
    return templates

@router.post("/create-template")
def create_template(
    template_data: TemplateSchema,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    try:
        data_dict = template_data.dict(exclude_unset=True)
        if "template_design" in data_dict and data_dict["template_design"]:
            if isinstance(data_dict["template_design"], (dict, list)):
                data_dict["template_design"] = json.dumps(data_dict["template_design"])
            elif isinstance(data_dict["template_design"], str):
                try:
                    json.loads(data_dict["template_design"])
                except json.JSONDecodeError:
                    raise HTTPException(
                        status_code=400,
                        detail="Invalid JSON string provided for template_design"
                    )

        template_entry = None
        if data_dict.get("template_id"):
            template_entry = db.query(EmailTemplate).filter(
                EmailTemplate.template_id == data_dict["template_id"]
            ).first()
        if template_entry:
            for key, value in data_dict.items():
                setattr(template_entry, key, value)
            db.commit()
            db.refresh(template_entry)
            message = "Template updated successfully"
        else:
            data_dict["owner"] = current_user.get("user_id")
            template_entry = EmailTemplate(**data_dict)
            db.add(template_entry)
            db.commit()
            db.refresh(template_entry)
            message = "Template created successfully"

        return {
            "message": message,
            "template_id": str(template_entry.template_id)
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/emails/{email_id}")
async def get_email_details(
    email_id: str,
    db: Session = Depends(get_db)
):
    try:
        email = db.query(EmailStore).filter(EmailStore.pk_id == email_id).first()

        if not email:
            return {"detail": "Email not found"}

        attachment_list = []
        if email.attachments:
            for att in email.attachments:
                blob_path = att["blob_path"]

                # Generate SAS token
                sas_token = settings.get_sas_token(blob_path)

                # Build blob download URL
                blob_url = (
                    f"{settings.BLOB_SERVICE_URI}"
                    f"{settings.ATTACHMENT_CONTAINER_NAME}/"
                    f"{blob_path}?{sas_token}"
                )

                # Append file details + SAS URL
                attachment_list.append({
                    "id": att["id"],
                    "file_name": att["file_name"],
                    "size": att["size"],
                    "mime_type": att["mime_type"],
                    "blob_path": blob_path,
                    "download_url": blob_url
                })

        return {
            "pk_id": email.pk_id,
            "sender": email.sender,
            "recipients": email.recipients,
            "cc": email.cc,
            "bcc": email.bcc,
            "subject": email.subject,
            "body": email.body,
            "body_format": email.body_format,
            "email_type": email.email_type,
            "received_datetime": email.received_datetime,
            "sent_datetime": email.sent_datetime,
            "headers": email.headers,
            "schedule_datetime": email.schedule_datetime,
            "status": email.status,
            "attachments": attachment_list
        }

    except Exception as e:
        logger.exception("Error fetching email details")
        raise HTTPException(status_code=500, detail=str(e))
    
@router.delete("/email-template/{template_id}")
def delete_template(template_id: str, db: Session = Depends(get_db)):
    try:
        template = db.query(EmailTemplate).filter(
            EmailTemplate.template_id == template_id
        ).first()

        if not template:
            raise HTTPException(status_code=404, detail="Template not found")

        db.delete(template)
        db.commit()

        return {"message": "Template deleted successfully", "template_id": template_id}

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))