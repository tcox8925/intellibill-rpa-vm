from sqlalchemy import Column, String, Text
from sqlalchemy.dialects.postgresql import UUID
from app.models.BaseClasses import Base
import uuid

class PchMemberVitals(Base):
    __tablename__ = "pch_member_vitals"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(
        UUID(as_uuid=True),
        default=uuid.uuid4,
        nullable=True,
        primary_key=True
    )
    member_id = Column(String(100), nullable=True)
    report_date = Column(Text, nullable=True)
    systolic = Column(String(20), nullable=True)
    diastolic = Column(String(20), nullable=True)
    source = Column(String(100), nullable=True)
    source_type = Column(String(100), nullable=True)

    
    def __repr__(self):
        values = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        return f"<PchMemberVitals {values}>"

