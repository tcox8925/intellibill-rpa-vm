from app.models.BaseClasses import Base
from sqlalchemy import Column, ForeignKey
import uuid
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER, NVARCHAR

class AgentDelegate(Base):
    __tablename__ = "agent_delegate"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    agent_id = Column(UNIQUEIDENTIFIER, ForeignKey("wpo.lup_agents.pk_id"), nullable=False)
    name = Column(NVARCHAR(100), nullable=False)
    email = Column(NVARCHAR(100), nullable=False)
    phone = Column(NVARCHAR(15), nullable=True)
    position = Column(NVARCHAR(255), nullable=True)
    permissions = Column(NVARCHAR(500), nullable=True)

    def __repr__(self):
        return (
            f"<AgentDelegate("
            f"name='{self.name}', "
            f"email='{self.email}', "
            f"phone='{self.phone}', "
            f"position='{self.position}', "
            f"permissions='{self.permissions}'"
            f")>"
        )