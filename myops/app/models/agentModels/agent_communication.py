from app.models.BaseClasses import Base
from sqlalchemy import Column, ForeignKey, DateTime, Boolean, func
import uuid
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER, VARCHAR

class AgentCommunication(Base):
    __tablename__ = "agent_communication"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    agent_id = Column(UNIQUEIDENTIFIER, ForeignKey("wpo.lup_agents.pk_id"), nullable=False)
    communication_id = Column(UNIQUEIDENTIFIER, ForeignKey("wpo.lup_communication_medium.pk_id"), nullable=False)
    value = Column(VARCHAR(255), nullable=False)
    extension = Column(VARCHAR(10), nullable=True)
    text_opt = Column(Boolean, default=False, nullable=False)
    dnd = Column(VARCHAR(20), nullable=True)
    ai_pre_recording = Column(Boolean, default=False, nullable=False)
    marketing_opt_in = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, server_default=func.getdate(), nullable=False)
    updated_at = Column(DateTime, nullable=True)
    primary = Column(Boolean, nullable=True, default=False)

    def __repr__(self):
        return (
            f"<AgentCommunication("
            f"agent_id='{self.agent_id}', communication_id='{self.communication_id}', "
            f"value='{self.value}', extension='{self.extension}', text_opt='{self.text_opt}', dnd='{self.dnd}', "
            f"ai_pre_recording='{self.ai_pre_recording}', marketing_opt_in='{self.marketing_opt_in}', "
            f"created_at='{self.created_at}', updated_at='{self.updated_at}'"
            f")>"
        )
