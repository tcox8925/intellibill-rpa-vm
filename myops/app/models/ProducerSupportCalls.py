from app.models.BaseClasses import Base
from sqlalchemy import Column, String, DateTime, Text, ForeignKey
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
import uuid
from datetime import datetime, timezone


class ProducerSupportCall(Base):
    __tablename__ = "producer_support_calls"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(
        UNIQUEIDENTIFIER,
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    caller_phone_number = Column(Text, nullable=False)
    recipient_phone_number = Column(Text, nullable=False)
    call_started_at = Column(DateTime, nullable=True)
    call_ended_at = Column(DateTime, nullable=True)
    extracted_details = Column(Text, nullable=True)
    transcript = Column(Text, nullable=False, default="")
    created_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    call_connection_id = Column(Text, nullable=True)
    call_status = Column(String(20), nullable=True)
    remarks = Column(Text, nullable=True)

    def __repr__(self):
        return f"<ProducerSupportCall(pk_id={self.pk_id}, caller_phone_number='{self.caller_phone_number}', call_status='{self.call_status}')>"
