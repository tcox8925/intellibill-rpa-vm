import uuid
from sqlalchemy import Column, String, ForeignKey, Boolean
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER, VARCHAR
from app.models.BaseClasses import Base  # adjust your Base import

class AgentAddress(Base):
    __tablename__ = "agent_address"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    agent_id = Column(UNIQUEIDENTIFIER, ForeignKey("wpo.lup_agents.pk_id"), nullable=False)
    address_type_id = Column(UNIQUEIDENTIFIER, ForeignKey("wpo.lup_addrtype.pk_id"), nullable=False)
    line1 = Column(VARCHAR(500), nullable=False)
    line2 = Column(VARCHAR(500), nullable=True)
    street = Column(VARCHAR(255), nullable=True)
    city = Column(VARCHAR(100), nullable=False)
    state = Column(VARCHAR(100), nullable=False)
    county = Column(VARCHAR(150), nullable=True)
    zip = Column(VARCHAR(20), nullable=True)
    primary = Column(Boolean, nullable=True, default=False)

    def __repr__(self):
        return (
            f"<AgentAddress("
            f"agent_id='{self.agent_id}', address_type_id='{self.address_type_id}', "
            f"line1='{self.line1}', line2='{self.line2}', street='{self.street}', city='{self.city}', "
            f"state='{self.state}', zip='{self.zip}'"
            f")>"
        )
