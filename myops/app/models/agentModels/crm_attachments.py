import uuid
from sqlalchemy import Boolean, Column, String, Text, DateTime, text, ForeignKey
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
from app.models.BaseClasses import Base
from sqlalchemy.dialects.postgresql import UUID


class CrmAttachments(Base):
    __tablename__ = "crm_attachments"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4, nullable=False)
    path = Column(Text, nullable=False)
    file_type = Column(String(255), nullable=True)
    time_stamp = Column(
        DateTime(timezone=True),
        server_default=text("TIMEZONE('UTC', CURRENT_TIMESTAMP)"),
        nullable=True,
    )
    user_id = Column(
        UNIQUEIDENTIFIER,
        ForeignKey("ops_sec.users.user_id"),
        nullable=False,
    )
    agent_id = Column(
        UNIQUEIDENTIFIER,
        ForeignKey("wpo.lup_agents.pk_id"),
        nullable=False,
    )
    npn = Column(String(20), nullable=False)
    source_id = Column(
        UUID(as_uuid=True),
        nullable=True,
    )
    is_private = Column(Boolean, nullable=True, default=False)

    def __repr__(self):
        return (
            f"<CrmAttachments(pk_id={self.pk_id}, file_type={self.file_type}, "
            f"agent_id={self.agent_id}, user_id={self.user_id}, npn={self.npn})>"
        )
