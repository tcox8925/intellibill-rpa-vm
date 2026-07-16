from app.models.BaseClasses import Base
from sqlalchemy.orm import relationship
import uuid
from sqlalchemy import Column, String, BigInteger, ForeignKey
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER, NVARCHAR

class AgentMarket(Base):
    __tablename__ = 'agent_market'
    __table_args__ = {'schema': 'wpo'}

    # Foreign key to Entity (BIGINT, not the GUID id)
    market_id = Column(
        UNIQUEIDENTIFIER,
        ForeignKey("wpo.lup_agent_market.pk_id"),
        nullable=False
    )

    # Primary Key (GUID)
    pk_id = Column(
        UNIQUEIDENTIFIER,
        primary_key=True,
        default=uuid.uuid4,
        nullable=False
    )

    agent_id = Column(UNIQUEIDENTIFIER, ForeignKey("wpo.lup_agents.pk_id"), nullable=False)

    # Unique bigint identifier
    market_intrest = Column(NVARCHAR(100), unique=True, nullable=False)

    def __repr__(self):
        return (
            f"""<AgentMarket(
            pk_id={self.pk_id},
            market_id={self.market_id},
            agent_id={self.agent_id},
            market_intrest={self.market_intrest}
            )>"""
        )