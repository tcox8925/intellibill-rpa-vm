import uuid
from sqlalchemy import Column, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.models.BaseClasses import Base


class Reports(Base):
    __tablename__ = "reports"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_id = Column(String(50), unique=True, nullable=False)
    report_name = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    created_by = Column(String(255), nullable=False)
    filters = Column(JSONB, nullable=False)
    selected_columns_order = Column(JSONB, nullable=True)
    entity_id = Column(String(255), nullable=True)
    sub_entity_id = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True),server_default=func.now(),nullable=False)

    def __repr__(self):
        return (
            f"<Report(pk_id={self.pk_id}, "
            f"report_name='{self.report_name}', "
            f"created_by='{self.created_by}')>"
        )
