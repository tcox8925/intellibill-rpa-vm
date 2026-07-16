import uuid
from sqlalchemy import Column, String, ForeignKey, Boolean
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER, VARCHAR
from app.models.BaseClasses import Base  # adjust your Base import

class AgentLanguages(Base):
    __tablename__ = "agent_languages"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    agent_id = Column(UNIQUEIDENTIFIER, ForeignKey("wpo.lup_agents.pk_id"), nullable=False)
    language_id = Column(UNIQUEIDENTIFIER, ForeignKey("wpo.lup_addrtype.pk_id"), nullable=False)
    preferred_language = Column(Boolean, nullable=False, default=False)

    def __repr__(self):
        return (
            f"<AgentAddress("
            f"agent_id='{self.agent_id}', language_id='{self.language_id}', "
            f")>"
        )
