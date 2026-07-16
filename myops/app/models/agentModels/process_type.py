from sqlalchemy import Column, DateTime, String, Date, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
import uuid
from app.models.BaseClasses import Base

class ProcessType(Base):
    __tablename__ = "process_type"
    __table_args__ = {"schema": "wpo"}

    process_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    process_type = Column(String(100), nullable=True)
    process_description = Column(String(255), nullable=True)
    owner = Column(String(255), nullable=True)
    status = Column(String, nullable=True)
    entity = Column(String(200), nullable=True)
    sub_entity = Column(String(200), nullable=True)
    teams_channel = Column(JSONB, nullable=False)

    def __repr__(self):
        values = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        return f"<ProcessType {values}>"