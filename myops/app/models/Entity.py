from app.models.BaseClasses import Base
from sqlalchemy.orm import relationship
import uuid
from sqlalchemy import JSON, Column, String, BigInteger, Text
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER

class Entity(Base):
	__tablename__ = 'entity'
	__schema__ = "ops_sec"
	__table_args__ = {'schema': __schema__}
 
	# Primary Key (GUID)
	id = Column(UNIQUEIDENTIFIER,
		primary_key=True,
		default=uuid.uuid4,
		nullable=False,
	)

	entity_name = Column(String(200), nullable=True)
	entity_affiliation = Column(String(100), nullable=True)
	entity_active_status = Column(String(20), nullable=True)
	entity_support_email = Column(String(200), nullable=True)
	entity_logo = Column(JSON, nullable=True)
	entity_address_1 = Column(String(200), nullable=True)
	entity_address_2 = Column(String(200), nullable=True)
	entity_zip_code = Column(String(20), nullable=True)
	entity_city = Column(String(100), nullable=True)
	entity_state = Column(String(100), nullable=True)

	# Unique bigint column
	entity_id = Column(Text, unique=True, nullable=True)
	sub_entities = relationship("Sub_Entity", back_populates="entity", cascade="all, delete-orphan")


	def __repr__(self):
		return (
			f"""<Entity(
			entity_id={self.entity_id}, 
			entity_name='{self.entity_name}', 
			entity_affiliation='{self.entity_affiliation}', 
			entity_logo='{self.entity_logo}', 
			entity_address_1='{self.entity_address_1}',
			entity_address_2='{self.entity_address_2}',
			entity_zip_code='{self.entity_zip_code}',
			entity_city='{self.entity_city}',
			entity_state='{self.entity_state}'
			)>"""
		)

	