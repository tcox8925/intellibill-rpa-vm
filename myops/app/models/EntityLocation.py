from app.models.BaseClasses import Base
from sqlalchemy import Column, String, Text, ForeignKey
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
import uuid
from sqlalchemy.orm import relationship
# from sqlalchemy.schema import FetchedValue

class Entity_Location(Base):
	__tablename__ = 'entity_location'
	__schema__ = "ops_sec"
	__table_args__ = {'schema': __schema__}

	id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
	sub_entity_id = Column(Text, ForeignKey(f"{__schema__}.sub_entity.sub_entity_id"))
	location_id = Column(Text, nullable=True)
	location_name = Column(String(200), nullable=True)
	location_address_1 = Column(String(200), nullable=True)
	location_address_2 = Column(String(200), nullable=True)
	location_city = Column(String(100), nullable=True)
	location_state = Column(String(100), nullable=True)
	location_zip = Column(String(20), nullable=True)

	location_entity_id_1_type = Column(String(50), nullable=True)
	location_entity_id_1 = Column(String(100), nullable=True)
	location_entity_id_2_type = Column(String(50), nullable=True)
	location_entity_id_2 = Column(String(100), nullable=True)
	location_entity_id_3_type = Column(String(50), nullable=True)
	location_entity_id_3 = Column(String(100), nullable=True)
	location_entity_id_4_type = Column(String(50), nullable=True)
	location_entity_id_4 = Column(String(100), nullable=True)

	# Relationship back to SubEntity
	sub_entity = relationship("Sub_Entity", back_populates="locations")


	def __repr__(self):
		return (
			f"""<Entity_Location(
			id={self.id},
			sub_entity_id={self.sub_entity_id},
			location_id={self.location_id}, 
			location_name='{self.location_name}', 
			location_address_1='{self.location_address_1}',
			location_address_2='{self.location_address_2}',
			location_city='{self.location_city}',
			location_state='{self.location_state}',
			location_zip='{self.location_zip}',
			location_entity_id_1_type='{self.location_entity_id_1_type}',
			location_entity_id_1='{self.location_entity_id_1}',
			location_entity_id_2_type='{self.location_entity_id_2_type}',
			location_entity_id_2='{self.location_entity_id_2}',
			location_entity_id_3_type='{self.location_entity_id_3_type}',
			location_entity_id_3='{self.location_entity_id_3}',
			location_entity_id_4_type='{self.location_entity_id_4_type}',
			location_entity_id_4='{self.location_entity_id_4}',
			)>"""
		)
