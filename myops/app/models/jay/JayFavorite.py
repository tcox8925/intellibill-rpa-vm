import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, Text, Integer
from sqlalchemy.dialects.postgresql import UUID
from app.models.BaseClasses import Base


class JayFavorite(Base):
    __tablename__ = "jay_favorites"
    __table_args__ = {"schema": "wpo"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    prompt_text = Column(Text, nullable=False)
    sql_fingerprint = Column(String(64), nullable=True)
    module = Column(String(50), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    use_count = Column(Integer, nullable=False, default=0)
    last_used_at = Column(DateTime(timezone=True), nullable=True)

    def __repr__(self):
        return (
            f"<JayFavorite(id={self.id}, user_id={self.user_id}, "
            f"prompt_text='{self.prompt_text[:50]}...', use_count={self.use_count})>"
        )
