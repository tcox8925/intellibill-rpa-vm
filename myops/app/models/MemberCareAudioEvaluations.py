from app.models.BaseClasses import Base
from sqlalchemy import Column, String, DateTime, BigInteger, Text, Integer, Float
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
import uuid
from datetime import datetime, timezone


class MembercareAgentAssessmentRecordings(Base):
    __tablename__ = "membercare_agent_assessment_recordings"
    __table_args__ = {"schema": "wpo"}

    id = Column(
        UNIQUEIDENTIFIER,
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    agent_login = Column(String(200), nullable=False)
    recorded_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
    phone_number = Column(String(15), nullable=False)
    campaign = Column(Text, nullable=True)
    file_name = Column(String(500), nullable=False)
    file_location = Column(Text, nullable=False)
    file_size = Column(BigInteger, nullable=False)
    created_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    uploaded_by = Column(String(200), nullable=True)
    detailed_score = Column(Text, nullable=True)
    total_score = Column(BigInteger, nullable=True)
    total_edited_score = Column(BigInteger, nullable=True)
    edited_at = Column(DateTime, nullable=True)
    edited_compliance_score = Column(String(10), nullable=True)
    call_status = Column(String(10), nullable=True)
    transcription = Column(Text, nullable=True)
    edited_by = Column(String(200), nullable=True)
    entity_id = Column(String(50), nullable=True)
    sub_entity_id = Column(String(50), nullable=True)
    sales_scorecard = Column(Text, nullable=True)
    max_total_score = Column(Integer, nullable=True)
    confidence_score = Column(Float, nullable=True)
    agent_name = Column(String(200), nullable=True)
    caller_name = Column(String(200), nullable=True)
    sale_or_not_sale = Column(String(20), nullable=True)
    edited_sale_or_not_sale = Column(String(20), nullable=True)
    edited_sale_reason = Column(Text, nullable=True)

    def __repr__(self):
        return f"<MembercareAgentAssessmentRecordings(id={self.id}, agent_login='{self.agent_login}', recorded_at='{self.recorded_at}', phone_number='{self.phone_number}', campaign='{self.campaign}', file_name='{self.file_name}', file_location='{self.file_location}', file_size={self.file_size}, created_at='{self.created_at}', updated_at='{self.updated_at}', uploaded_by='{self.uploaded_by}', detailed_score='{self.detailed_score}', total_score={self.total_score}, total_edited_score={self.total_edited_score}, edited_at='{self.edited_at}', call_status='{self.call_status}', transcription='{self.transcription}')>"
