from app.models.BaseClasses import Base
from sqlalchemy import Column, String, DateTime, Text, ForeignKey
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
import uuid
from datetime import datetime, timezone


class MembercareAssessmentRecordingNotes(Base):
    __tablename__ = "membercare_assessment_recording_notes"
    __table_args__ = {"schema": "wpo"}

    id = Column(
        UNIQUEIDENTIFIER,
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    recording_id = Column(
        UNIQUEIDENTIFIER,
        ForeignKey("wpo.membercare_agent_assessment_recordings.id"),
        nullable=False,
    )
    user_login = Column(String(200), nullable=False)
    note_text = Column(Text, nullable=False)
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
        return f"<MembercareAssessmentRecordingNotes(id={self.id}, recording_id={self.recording_id}, user_login='{self.user_login}', note_text='{self.note_text}', created_at='{self.created_at}', updated_at='{self.updated_at}')>"
