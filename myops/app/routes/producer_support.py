import os

import traceback
from fastapi import APIRouter, Depends, HTTPException, Query, Header, Body
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional, List
from datetime import datetime, timezone
from app.db.session import get_db


def _utc_iso(dt) -> Optional[str]:
    """Format a datetime as ISO string with UTC offset."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()
from app.models.ProducerSupportCalls import ProducerSupportCall
from uuid import UUID
from app.models.Emails.EmailModal import EmailContent, EmailService
from .email_templates import get_otp_email_body
from pydantic import BaseModel, EmailStr

router = APIRouter(tags=["PRODUCER SUPPORT ROUTES"])
SECRET_KEY = os.getenv("MYOPS_APP_SECRET_KEY", "")


@router.get("/producer-support-calls")
def get_producer_support_calls(
    caller_phone_number: Optional[str] = Query(
        None, description="Filter by caller phone number"
    ),
    call_status: Optional[str] = Query(None, description="Filter by call status"),
    call_started_at_from: Optional[datetime] = Query(
        None, description="Filter calls started from this datetime"
    ),
    call_started_at_to: Optional[datetime] = Query(
        None, description="Filter calls started until this datetime"
    ),
    call_ended_at_from: Optional[datetime] = Query(
        None, description="Filter calls ended from this datetime"
    ),
    call_ended_at_to: Optional[datetime] = Query(
        None, description="Filter calls ended until this datetime"
    ),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(100, ge=1, le=200, description="Number of records per page"),
    db: Session = Depends(get_db),
):
    """
    Get grouped records from producer support calls table.
    Groups by caller_phone_number and returns call count with pagination.

    Optional filters:
    - caller_phone_number: Filter by specific caller phone number
    - call_status: Filter by call status
    - call_started_at_from: Filter calls that started on or after this datetime
    - call_started_at_to: Filter calls that started on or before this datetime
    - call_ended_at_from: Filter calls that ended on or after this datetime
    - call_ended_at_to: Filter calls that ended on or before this datetime
    - page: Page number for pagination (default: 1)
    - page_size: Number of records per page (default: 100)
    """
    try:
        # Start building the query - group by caller_phone_number
        query = db.query(
            ProducerSupportCall.caller_phone_number,
            func.count(ProducerSupportCall.pk_id).label("call_count"),
        )

        # Apply optional filters
        if caller_phone_number:
            query = query.filter(
                ProducerSupportCall.caller_phone_number == caller_phone_number
            )

        if call_status:
            query = query.filter(ProducerSupportCall.call_status == call_status)

        if call_started_at_from:
            query = query.filter(
                ProducerSupportCall.call_started_at >= call_started_at_from
            )

        if call_started_at_to:
            query = query.filter(
                ProducerSupportCall.call_started_at <= call_started_at_to
            )

        if call_ended_at_from:
            query = query.filter(
                ProducerSupportCall.call_ended_at >= call_ended_at_from
            )

        if call_ended_at_to:
            query = query.filter(ProducerSupportCall.call_ended_at <= call_ended_at_to)

        # Group by caller_phone_number
        query = query.group_by(ProducerSupportCall.caller_phone_number)

        # Order by call count descending (most calls first)
        query = query.order_by(func.count(ProducerSupportCall.pk_id).desc())

        # Get total count before pagination
        total_count = query.count()

        # Apply pagination
        offset = (page - 1) * page_size
        grouped_records = query.offset(offset).limit(page_size).all()

        # Calculate total pages
        total_pages = (total_count + page_size - 1) // page_size

        return {
            "data": [
                {
                    "caller_phone_number": record.caller_phone_number,
                    "call_count": record.call_count,
                }
                for record in grouped_records
            ],
            "pagination": {
                "page_number": page,
                "page_size": page_size,
                "total_count": total_count,
                "total_pages": total_pages,
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching producer support calls: {str(e)}"
        )


@router.get("/producer-support-calls/{caller_phone_number}")
def get_producer_support_calls_by_phone(
    caller_phone_number: str,
    call_status: Optional[str] = Query(None, description="Filter by call status"),
    call_started_at_from: Optional[datetime] = Query(
        None, description="Filter calls started from this datetime"
    ),
    call_started_at_to: Optional[datetime] = Query(
        None, description="Filter calls started until this datetime"
    ),
    call_ended_at_from: Optional[datetime] = Query(
        None, description="Filter calls ended from this datetime"
    ),
    call_ended_at_to: Optional[datetime] = Query(
        None, description="Filter calls ended until this datetime"
    ),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(100, ge=1, le=200, description="Number of records per page"),
    db: Session = Depends(get_db),
):
    """
    Fetch detailed records for a specific caller phone number from the ProducerSupportCall table.
    Returns all detailed information for the specified caller_phone_number with pagination.

    Path parameters:
    - caller_phone_number: The caller phone number to filter by

    Optional query parameters:
    - call_status: Filter by call status
    - call_started_at_from: Filter calls started from this datetime
    - call_started_at_to: Filter calls started until this datetime
    - call_ended_at_from: Filter calls ended from this datetime
    - call_ended_at_to: Filter calls ended until this datetime
    - page: Page number (1-indexed, default: 1)
    - page_size: Number of records per page (default: 100)
    """
    try:
        # Start with base query for the caller phone number
        query = db.query(ProducerSupportCall).filter(
            ProducerSupportCall.caller_phone_number == caller_phone_number
        )

        # Apply optional filters
        if call_status:
            query = query.filter(ProducerSupportCall.call_status == call_status)

        if call_started_at_from:
            query = query.filter(
                ProducerSupportCall.call_started_at >= call_started_at_from
            )

        if call_started_at_to:
            query = query.filter(
                ProducerSupportCall.call_started_at <= call_started_at_to
            )

        if call_ended_at_from:
            query = query.filter(
                ProducerSupportCall.call_ended_at >= call_ended_at_from
            )

        if call_ended_at_to:
            query = query.filter(ProducerSupportCall.call_ended_at <= call_ended_at_to)

        # Order by most recent call first
        query = query.order_by(ProducerSupportCall.call_started_at.desc())

        # Get total count before pagination
        total_count = query.count()

        # Apply pagination
        offset = (page - 1) * page_size
        records = query.offset(offset).limit(page_size).all()

        if not records and page == 1:
            raise HTTPException(
                status_code=404,
                detail=f"No records found for caller_phone_number: {caller_phone_number}",
            )

        # Format response
        result = []
        for record in records:
            result.append(
                {
                    "pk_id": str(record.pk_id),
                    "caller_phone_number": record.caller_phone_number,
                    "recipient_phone_number": record.recipient_phone_number,
                    "call_started_at": _utc_iso(record.call_started_at),
                    "call_ended_at": _utc_iso(record.call_ended_at),
                    "extracted_details": record.extracted_details,
                    "transcript": record.transcript,
                    "call_connection_id": record.call_connection_id,
                    "call_status": record.call_status,
                    "remarks": record.remarks,
                    "created_at": _utc_iso(record.created_at),
                    "updated_at": _utc_iso(record.updated_at),
                }
            )

        # Calculate total pages
        total_pages = (total_count + page_size - 1) // page_size

        return {
            "data": result,
            "pagination": {
                "page_number": page,
                "page_size": page_size,
                "total_count": total_count,
                "total_pages": total_pages,
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching call details: {str(e)}"
        )


@router.get("/producer-support-calls/{caller_phone_number}/{call_id}")
def get_producer_support_call_by_id(
    caller_phone_number: str,
    call_id: str,
    db: Session = Depends(get_db),
):
    """
    Get detailed information about a specific producer support call by caller phone number and call ID.

    Path parameters:
    - caller_phone_number: The caller phone number
    - call_id: UUID of the specific call record
    """
    try:
        # Validate UUID format
        try:
            uuid_obj = UUID(call_id)
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Invalid call_id format. Must be a valid UUID."
            )

        # Query the call by both caller_phone_number and pk_id
        call = (
            db.query(ProducerSupportCall)
            .filter(
                ProducerSupportCall.caller_phone_number == caller_phone_number,
                ProducerSupportCall.pk_id == uuid_obj,
            )
            .first()
        )

        if not call:
            raise HTTPException(
                status_code=404,
                detail=f"Call with ID {call_id} not found for caller {caller_phone_number}",
            )

        # Return detailed call information
        return {
            "pk_id": str(call.pk_id),
            "caller_phone_number": call.caller_phone_number,
            "recipient_phone_number": call.recipient_phone_number,
            "call_started_at": _utc_iso(call.call_started_at),
            "call_ended_at": _utc_iso(call.call_ended_at),
            "extracted_details": call.extracted_details,
            "transcript": call.transcript,
            "call_connection_id": call.call_connection_id,
            "call_status": call.call_status,
            "remarks": call.remarks,
            "created_at": _utc_iso(call.created_at),
            "updated_at": _utc_iso(call.updated_at),
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching call details: {str(e)}"
        )


@router.post("/producer-support-calls/send-email-otp")
async def send_email_otp(
    email: str,
    otp: str,
    x_auth_token: str = Header(..., alias="x-auth-token"),
):
    """
    Endpoint to send OTP to the specified email address.
    Requires x-auth-token header for authentication.
    This is a placeholder implementation. Integrate with actual email service.
    """
    # Validate the secret key
    if x_auth_token != SECRET_KEY:
        raise HTTPException(status_code=401, detail="Invalid authentication token")

    try:
        email_service = EmailService()
        await email_service.send_email(
            EmailContent(
                sender="dataops@834labs.com",
                body=get_otp_email_body(otp),
                subject="Your OTP Code",
                to=[email],
                html=True,
            )
        )
        return {"message": f"OTP sent to {email}"}
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error sending OTP email: {str(e)}"
        )


@router.post("/producer-support-calls/send-onboarding-email")
async def send_onboarding_email(
    email: str,
    name: str,
    x_auth_token: str = Header(..., alias="x-auth-token"),
):
    """
    Endpoint to send onboarding email to the specified email address.
    Requires x-auth-token header for authentication.
    This is a placeholder implementation. Integrate with actual email service.
    """
    # Validate the secret key
    if x_auth_token != SECRET_KEY:
        raise HTTPException(status_code=401, detail="Invalid authentication token")

    try:
        email_service = EmailService()
        email_body = f"""<html>
                <body style="font-family: Arial, sans-serif; line-height: 1.2;">
                    <h2>Welcome to MyOps360, {name}!</h2>
                    <p>We're excited to have you on board.</p>
                    <p>If you have any questions, feel free to reach out to our support team.</p>
                    <p>Thanks,<br/>The MyOps360 Team</p>
                </body>
            </html>
        """
        await email_service.send_email(
            EmailContent(
                sender="dataops@834labs.com",
                body=email_body,
                subject="Welcome to MyOps360",
                to=[email],
                html=True,
            )
        )
        return {"message": f"Onboarding email sent to {email}"}
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status_code=500, detail=f"Error sending onboarding email: {str(e)}"
        )


class SendEmailRequest(BaseModel):
    to: List[EmailStr]
    subject: str
    body: str
    html: bool = True
    cc: Optional[List[EmailStr]] = None
    bcc: Optional[List[EmailStr]] = None
    sender: Optional[EmailStr] = None


@router.post("/producer-support-calls/send-email")
async def send_email(
    payload: SendEmailRequest,
    x_auth_token: str = Header(..., alias="x-auth-token"),
):
    """
    Generic email endpoint for the call center agent.
    Sends an email with the provided subject, body, recipients, cc, and bcc.
    Requires x-auth-token header for authentication.
    """
    if x_auth_token != SECRET_KEY:
        raise HTTPException(status_code=401, detail="Invalid authentication token")

    try:
        email_service = EmailService()
        email_content = EmailContent(
            sender=payload.sender or "dataops@834labs.com",
            to=payload.to,
            subject=payload.subject,
            body=payload.body,
            html=payload.html,
            cc=payload.cc,
            bcc=payload.bcc,
        )
        result = await email_service.send_email(email_content)
        return {
            "message": f"Email sent successfully to {', '.join(payload.to)}",
            "status": "sent",
        }
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status_code=500, detail=f"Error sending email: {str(e)}"
        )
