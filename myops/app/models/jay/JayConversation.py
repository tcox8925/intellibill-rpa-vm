import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.models.BaseClasses import Base


class JayConversation(Base):
    __tablename__ = "jay_conversations"
    __table_args__ = {"schema": "wpo"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    title = Column(String(200), nullable=False, default="New Conversation")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    is_archived = Column(Boolean, nullable=False, default=False)
    entity_id = Column(String, nullable=True, index=True)
    entity_name = Column(String(200), nullable=True)

    messages = relationship(
        "JayMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="JayMessage.created_at",
    )

    def __repr__(self):
        return (
            f"<JayConversation(id={self.id}, user_id={self.user_id}, "
            f"title='{self.title}', created_at='{self.created_at}')>"
        )
