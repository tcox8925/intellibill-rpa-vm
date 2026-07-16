import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, Text, ForeignKey, SmallInteger
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.models.BaseClasses import Base


class JayMessage(Base):
    __tablename__ = "jay_messages"
    __table_args__ = {"schema": "wpo"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("wpo.jay_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role = Column(String(20), nullable=False)  # 'user' | 'assistant' | 'system'
    content = Column(Text, nullable=False)
    format = Column(
        String(20), nullable=False, default="text"
    )  # 'text' | 'bar_chart' | 'pie_chart' | 'table' | 'kpi' | 'error'
    data = Column(JSONB, nullable=True)  # chart/table data payload
    sql_fingerprint = Column(String(64), nullable=True)
    action = Column(String(30), nullable=True)  # 'navigate' | 'filter' | 'navigate_and_filter'
    action_data = Column(JSONB, nullable=True)  # route, filters for navigation actions
    rating = Column(SmallInteger, nullable=True)  # NULL=unrated, 1=thumbs up, -1=thumbs down
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    conversation = relationship("JayConversation", back_populates="messages")

    def __repr__(self):
        return (
            f"<JayMessage(id={self.id}, conversation_id={self.conversation_id}, "
            f"role='{self.role}', format='{self.format}')>"
        )
