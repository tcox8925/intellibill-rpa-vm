from sqlalchemy import Column, BigInteger, VARCHAR, TIMESTAMP, text, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID
from app.models.BaseClasses import Base
from uuid import uuid4


class ComAuditHistory(Base):
    __tablename__ = "com_audit_history"
    __table_args__ = {"schema": "wpo"}

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    audit_id = Column(UUID(as_uuid=True), nullable=False, default=uuid4)

    txn_id = Column(VARCHAR(36), nullable=True)

    created_at = Column(
        TIMESTAMP,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP")
    )

    user_email = Column(VARCHAR(200), nullable=False)
    user_id = Column(BigInteger, nullable=False)

    action_message = Column(Text, nullable=True)
    action = Column(VARCHAR(50), nullable=True)
    tab = Column(VARCHAR(50), nullable=True)
    module = Column(VARCHAR(50), nullable=True)
    sub_module = Column(VARCHAR(50), nullable=True)

    def __repr__(self):
        return (
            f"<ComAuditHistory(id={self.id}, audit_id={self.audit_id}, "
            f"user_email={self.user_email}, action={self.action}, action_message={self.action_message})>"
        )
