from sqlalchemy import Column, DateTime, String, Date, Text, ForeignKey, Boolean, JSON
from sqlalchemy.dialects.postgresql import UUID
import uuid
from app.models.BaseClasses import Base

class ProcessTypeUsers(Base):
    __tablename__ = "process_type_users"
    __table_args__ = {"schema": "wpo"}


    pk_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    process_id = Column(UUID(as_uuid=True), ForeignKey("wpo.process_type.process_id"), nullable=False)
    user_email = Column(String, nullable=True)
    email = Column(Boolean, nullable=True)

    def __repr__(self):
        values = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        return f"<ProcessTypeUsers {values}>"
    
