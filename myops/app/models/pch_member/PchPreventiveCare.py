from sqlalchemy import Column, String
from sqlalchemy.dialects.postgresql import UUID
from app.models.BaseClasses import Base
import uuid

class PchMemberPreventiveCare(Base):
    __tablename__ = "pch_member_preventive_care"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    member_id = Column(String(100), nullable=True)
    status = Column(String(20), nullable=True)
    care_type = Column(String(100), nullable=True)
    care_name = Column(String(100), nullable=True)
    diagnosis = Column(String(200), nullable=True)
    onboarding_date = Column(String(20), nullable=True)
    source = Column(String(100), nullable=True)
    source_type = Column(String(100), nullable=True)
    
    def __repr__(self):
        values = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        return f"<PchMemberPreventiveCare {values}>"
