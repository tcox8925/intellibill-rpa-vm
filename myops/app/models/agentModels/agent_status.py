from app.models.BaseClasses import Base
from sqlalchemy import Column, String
import uuid
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER

class AgentStatus(Base):
    __tablename__ = "lup_status"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)

    company_id = Column(String, primary_key=True)
    company_name = Column(String)
    status = Column(String)

    def __repr__(self):
        return (
            f"<LupStatus("
            f"company_id='{self.company_id}', "
            f"company_name='{self.company_name}', "
            f"status='{self.status}'"
            f")>"
        )
