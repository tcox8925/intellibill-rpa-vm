import uuid
from sqlalchemy import Column, String, Text, TIMESTAMP, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import text
from app.models.BaseClasses import Base


class TicketsAuditHistory(Base):
    __tablename__ = "tickets_audit_history"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    created_at = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')")
    )

    user_id = Column(
        UUID(as_uuid=True),
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
        String(30),
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

    ticket_id = Column(
        UUID(as_uuid=True),
        ForeignKey("wpo.crm_tickets.pk_id"),
        nullable=False
    )

    def __repr__(self):
        return f"<TicketsAuditHistory(pk_id={self.pk_id}, ticket_id={self.ticket_id}, action={self.action})>"
