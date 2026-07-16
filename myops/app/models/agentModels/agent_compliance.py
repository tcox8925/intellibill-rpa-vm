from app.models.BaseClasses import Base
from sqlalchemy import Boolean, Column, DateTime, String, Text, text
from sqlalchemy.dialects.postgresql import UUID

class AgentCompliance(Base):
    __tablename__ = "lup_agent_compliance"
    __table_args__ = {"schema": "wpo"}
    
    pk_id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"), nullable=False)
    inquiry_id = Column(String, index=True)
    npn = Column(String(50), nullable=True)
    comp_owner = Column(String(255), nullable=True)
    created_by = Column(String(255), nullable=True)
    modified_by = Column(String(255), nullable=True)
    created_time = Column(DateTime, nullable=True)
    modified_time = Column(DateTime, nullable=True)
    last_activity_time = Column(DateTime, nullable=True)
    date_received = Column(DateTime, nullable=True)
    due_date = Column(DateTime, nullable=True)
    related_carrier = Column(String(255), nullable=True)
    date_resolved = Column(DateTime, nullable=True)
    agent_name = Column(String(255), nullable=True)
    inquiry_type = Column(String(255), nullable=True)
    comm_sent_to_agent = Column(String, nullable=True)
    comm_sent_to_agency = Column(String, nullable=True)
    locked = Column(Boolean, nullable=True)
    dir_upline = Column(String(255), nullable=True)
    agent_email = Column(String(255), nullable=True)
    inquiry_status = Column(String(255), nullable=True)
    received_from = Column(String(255), nullable=True)
    inquiry_description = Column(String(2000), nullable=True)
    subject_of_inquiry = Column(Text, nullable=True)
    subject_email = Column(Text, nullable=True)
    carrier_case_no = Column(Text, nullable=True)
    agent_status = Column(String(255), nullable=True)

    def __repr__(self):
        values = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        return f"<AgentCompliance {values}>"