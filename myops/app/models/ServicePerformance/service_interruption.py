from sqlalchemy import Column, DateTime, String, Date, Text, Numeric
from sqlalchemy.dialects.postgresql import UUID
import uuid
from app.models.BaseClasses import Base

class ServiceInterruption(Base):
    __tablename__ = "service_interruption"
    __schema__ = "ops_srv"
    __table_args__ = {"schema": __schema__}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    report_date = Column(String, nullable=True)
    process_id = Column(UUID(as_uuid=True), nullable=False)
    process_name = Column(String(255), nullable=False)
    carrier_id = Column(String(100), nullable=True)
    carrier_name = Column(String(255), nullable=True)
    raw_file_name = Column(String(500), nullable=True)
    received = Column(String, nullable=True)
    processed = Column(String, nullable=True)
    issue_description = Column(Text, nullable=True)
    resolution_description = Column(Text, nullable=True)
    issue_status = Column(String(100), nullable=True)
    issue_date = Column(String, nullable=True)
    resolution_date = Column(String, nullable=True)
    cadence = Column(String(100), nullable=True)
    interruption_id = Column(Numeric, nullable=False, server_default="nextval('wpo.interruption_id_seq')")
    entity_id = Column(Text, nullable=True)
    sub_entity_id = Column(Text, nullable=True)
    buisness_entity = Column(String(20), nullable=True)
    buisness_sub_entity = Column(String(20), nullable=True)
    business_lead = Column(String(200), nullable=True)
    issue_count = Column(Numeric, nullable=True, default=1)

    def __repr__(self):
        values = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        return f"<ServiceInterruption {values}>"
