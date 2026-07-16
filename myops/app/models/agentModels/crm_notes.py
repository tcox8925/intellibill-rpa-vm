import uuid
from sqlalchemy import Column, String, Text, DateTime, text, ForeignKey, Boolean
from app.models.BaseClasses import Base
from sqlalchemy.dialects.postgresql import UUID


class CrmNotes(Base):
    __tablename__ = "crm_notes"
    __table_args__ = {"schema": "wpo"}
    pk_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )
    type = Column(String(50), nullable=False)
    description = Column(Text, nullable=False)
    time_stamp = Column(
        DateTime(timezone=True),
        server_default=text("TIMEZONE('UTC', CURRENT_TIMESTAMP)"),
        nullable=True,
    )
    user_id = Column(
         UUID(as_uuid=True),
        ForeignKey("ops_sec.users.user_id"),
        nullable=False,
    )
    agent_id = Column(
        UUID(as_uuid=True),
        ForeignKey("wpo.lup_agents.pk_id"),
        nullable=False,
    )
    source_id = Column(
        UUID(as_uuid=True),
        nullable=True,
    )
    is_private = Column(Boolean, nullable=True)
    sub_type = Column(String(50), nullable=True)
    agent_npn = Column(String(25), nullable=True)

    def __repr__(self):
        return (
            f"<CrmNotes(pk_id={self.pk_id}, type={self.type}, "
            f"agent_id={self.agent_id}, user_id={self.user_id}, is_private={self.is_private}, sub_type={self.sub_type}, agent_npn={self.agent_npn})>"
        )
