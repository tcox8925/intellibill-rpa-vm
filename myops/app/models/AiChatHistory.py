from app.models.BaseClasses import Base
from sqlalchemy import Column, String, DateTime, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
import uuid
from datetime import datetime, timezone


class AIChatHistory(Base):
    __tablename__ = "ai_chat_history"
    __table_args__ = {"schema": "wpo"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    bot_type = Column(String(64), nullable=False, index=True)
    ip_address = Column(String(64), nullable=True)

    session_start = Column(DateTime(timezone=True), nullable=False)
    session_end = Column(DateTime(timezone=True), nullable=True)

    # ✅ required by checklist ("in progress", "ended")
    session_status = Column(String(32), nullable=False, default="in progress")

    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    # stored as JSON strings
    chat_history = Column(Text, nullable=False, default="[]")        # list[str]
    extracted_details = Column(Text, nullable=False, default="{}")   # dict json

    # ✅ OTP stored in DB
    otp_code = Column(String(16), nullable=True)
    otp_expires_at = Column(DateTime(timezone=True), nullable=True)
    otp_attempts = Column(Integer, nullable=False, default=0)

    def __repr__(self):
        return (
            f"<AIChatHistory(id={self.id}, session_start='{self.session_start}', "
            f"session_end='{self.session_end}', created_at='{self.created_at}', "
            f"updated_at='{self.updated_at}', ip_address='{self.ip_address}', "
            f"bot_type='{self.bot_type}', chat_history='{self.chat_history}', "
            f"extracted_details='{self.extracted_details}')>"
        )