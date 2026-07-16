from app.models.BaseClasses import Base
from sqlalchemy import Column
import uuid
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER, NVARCHAR

class LupAgentPref(Base):
    __tablename__ = "lup_agent_pref"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    preference = Column(NVARCHAR(255))

    def __repr__(self):
        return (
            f"<LupAgentPref("
            f"preference='{self.preference}'"
            f")>"
        )