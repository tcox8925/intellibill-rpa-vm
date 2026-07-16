from app.models.BaseClasses import Base
from sqlalchemy.orm import relationship
import uuid
from sqlalchemy import Column, String, BigInteger, ForeignKey
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER, NVARCHAR

class AgentGrowthPref(Base):
    __tablename__ = 'agent_growth_pref'
    __table_args__ = {'schema': 'wpo'}

    # Foreign key to Entity (BIGINT, not the GUID id)
    growth_pref_id = Column(
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
    growth_pref = Column(NVARCHAR(255), unique=True, nullable=False)

    def __repr__(self):
        return (
            f"""<AgentGrowthPref(
            pk_id={self.pk_id},
            growth_pref_id={self.growth_pref_id},
            agent_id={self.agent_id},
            growth_pref={self.growth_pref}
            )>"""
        )