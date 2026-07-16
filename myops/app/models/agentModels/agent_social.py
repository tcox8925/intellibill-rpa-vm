from app.models.BaseClasses import Base
from sqlalchemy.orm import relationship
import uuid
from sqlalchemy import Column, String, BigInteger, ForeignKey
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER, NVARCHAR

class AgentSocial(Base):
	__tablename__ = 'crm_agent_social'
	__table_args__ = {'schema': 'wpo'}

	# Foreign key to Entity (BIGINT, not the GUID id)
	platform_id = Column(
		UNIQUEIDENTIFIER,
		ForeignKey("wpo.lup_social.pk_id"),
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
	url = Column(NVARCHAR(None), unique=True, nullable=False)

	def __repr__(self):
		return (
			f"""<Agent_Social(
			pk_id={self.pk_id},
            platform_id={self.platform_id},
			)>"""
		)