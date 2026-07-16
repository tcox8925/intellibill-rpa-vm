from app.models.BaseClasses import Base
from sqlalchemy import Column, String, DateTime, ForeignKey
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
import uuid
from datetime import datetime, timezone


class MembercareAgent(Base):
    __tablename__ = "membercare_agent"
    __table_args__ = {"schema": "wpo"}

    id = Column(
        UNIQUEIDENTIFIER,
        ForeignKey("ops_sec.users.user_id", ondelete="CASCADE"),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    email = Column(String(200), nullable=False, unique=True)
    name = Column(String(200), nullable=False)
    status = Column(String(20), nullable=True)
    npn = Column(String(20), nullable=True, unique=True)
    phone = Column(String(25), nullable=True)
    supervisor = Column(String(200), nullable=True)
    active_since = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=True,
    )
    languages = Column(String(200), nullable=True)
    location = Column(String(200), nullable=True)

    def __repr__(self):
        return f"<MembercareAgent(id={self.id}, name='{self.name}', email='{self.email}')>"
