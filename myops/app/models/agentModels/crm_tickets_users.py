import uuid
from sqlalchemy import Column, String, DateTime, ForeignKey, text
from sqlalchemy.dialects.postgresql import UUID
from app.models.BaseClasses import Base


class CrmTicketsUsers(Base):
    __tablename__ = "crm_tickets_users"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )

    status = Column(String(20), nullable=True)

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ops_sec.users.user_id"),
        nullable=True,
    )

    time_stamp = Column(
        DateTime(timezone=True),
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=True,
    )

    def __repr__(self):
        return (
            f"<CrmTicketsUsers(pk_id={self.pk_id}, "
            f"status={self.status}, user_id={self.user_id})>"
        )
