from sqlalchemy import Column, String, Text
from sqlalchemy.dialects.postgresql import UUID
from app.models.BaseClasses import Base
import uuid

class PchMemberImmunization(Base):
    __tablename__ = "pch_member_immunization"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(
        UUID(as_uuid=True),
        default=uuid.uuid4,
        nullable=True,
        primary_key=True
    )
    member_id = Column(String(100), nullable=True)
    status = Column(String(100), nullable=True)
    fields = Column(String(100), nullable=True)
    complete_date = Column(String(100), nullable=True)
    source = Column(String(100), nullable=True)
    immunization_id = Column(UUID(as_uuid=True), nullable=True)
    
    def __repr__(self):
        values = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        return f"<PchMemberImmunization {values}>"

