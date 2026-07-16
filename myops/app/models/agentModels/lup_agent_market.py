from app.models.BaseClasses import Base
from sqlalchemy import Column
import uuid
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER, NVARCHAR

class LupAgentMarket(Base):
    __tablename__ = "lup_agent_market"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    market = Column(NVARCHAR(255))

    def __repr__(self):
        return (
            f"<LupAgentMarket("
            f"market='{self.market}'"
            f")>"
        )