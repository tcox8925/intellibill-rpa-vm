import uuid
from sqlalchemy import VARCHAR, Column, String, Text, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import text
from app.models.BaseClasses import Base


class CRMAuditHistory(Base):
    __tablename__ = "crm_audit_history"
    __table_args__ = {"schema": "wpo"}

    audit_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    agent_id = Column(
        UUID(as_uuid=True),
        nullable=True
    )

    created_at = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("TIMEZONE('utc', now())")
    )

    login_user = Column(
        String(255),
        nullable=False
    )

    action_message = Column(
        Text,
        nullable=True
    )

    action = Column(
        String(50),
        nullable=True
    )

    tab = Column(
        String(50),
        nullable=True
    )

    sub_module = Column(
        String(50),
        nullable=True
    )

    module = Column(
        String(50),
        nullable=True
    )

    sub_entity_id = Column(
        String(30),
        nullable=True
    )

    entity_id = Column(
        String(30),
        nullable=False
    )
    npn = Column(
        VARCHAR(30),
        nullable=True
    )
    source_id = Column(
        UUID(as_uuid=True),
        nullable=True,
    )

    def __repr__(self):
        values = {c.name: getattr(self, c.name)
                  for c in self.__table__.columns}
        return f"<CRMAuditHistory {values}>"
