from app.models.BaseClasses import Base
from sqlalchemy import Column, String, DateTime, Text, ForeignKey
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
import uuid
from datetime import datetime, timezone


class MembercareCustomer(Base):
    __tablename__ = "membercare_customer"
    __table_args__ = {"schema": "wpo"}

    id = Column(
        UNIQUEIDENTIFIER,
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    agent_pk_id = Column(
        UNIQUEIDENTIFIER,
        ForeignKey("wpo.lup_agents.pk_id"),
        nullable=True,
    )
    caller_name = Column(String(200), nullable=True)
    caller_phone = Column(String(20), nullable=True)
    caller_gender = Column(String(20), nullable=True)
    caller_dob = Column(String(20), nullable=True)
    caller_address = Column(Text, nullable=True)
    caller_email = Column(String(200), nullable=True)
    caller_zip = Column(String(10), nullable=True)
    caller_county = Column(String(100), nullable=True)
    caller_ssn = Column(String(11), nullable=True)
    created_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self):
        return f"<MembercareCustomer(id={self.id}, agent_pk_id={self.agent_pk_id}, caller_name='{self.caller_name}')>"
