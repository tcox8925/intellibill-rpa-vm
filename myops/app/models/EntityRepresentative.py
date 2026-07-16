from app.models.BaseClasses import Base
from sqlalchemy.orm import relationship
from sqlalchemy import Column, String, Integer, Text, ForeignKey
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
import uuid

class Entity_Representative(Base):
	__tablename__ = 'entity_representative'
	__schema__ = "ops_sec" 
	__table_args__ = {'schema': __schema__}

	id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)

	sub_entity_id = Column(Text, ForeignKey(f"{__schema__}.sub_entity.sub_entity_id"), nullable=False)
	rep_id = Column(Text, unique=True, nullable=False)  # since rep_id looks like a business key

	rep_lname = Column(String(100), nullable=False)
	rep_fname = Column(String(100), nullable=False)
	rep_address_1 = Column(String(200), nullable=False)
	rep_address_2 = Column(String(200), nullable=True)
	rep_city = Column(String(100), nullable=True)
	rep_state = Column(String(100), nullable=True)
	rep_zip = Column(String(20), nullable=True)

	rep_entity_id_1_type = Column(String(50), nullable=True)
	rep_entity_id_1 = Column(String(100), nullable=True)
	rep_entity_id_2_type = Column(String(50), nullable=True)
	rep_entity_id_2 = Column(String(100), nullable=True)
	rep_entity_id_3_type = Column(String(50), nullable=True)
	rep_entity_id_3 = Column(String(100), nullable=True)
	rep_entity_id_4_type = Column(String(50), nullable=True)
	rep_entity_id_4 = Column(String(100), nullable=True)

	# Relationship back to SubEntity
	sub_entity = relationship("Sub_Entity", back_populates="representatives")

	def __repr__(self):
		return (
			f"""<Entity_Representative(
			sub_entity_id={self.sub_entity_id}
			rep_id={self.rep_id}, 
			rep_lname='{self.rep_lname}', 
			rep_fname='{self.rep_fname}',
			rep_address_1='{self.rep_address_1}',
			rep_address_2='{self.rep_address_2}',
			rep_city='{self.rep_city}',
			rep_state='{self.rep_state}',
			rep_zip='{self.rep_zip}',
			rep_entity_id_1_type='{self.rep_entity_id_1_type}',
			rep_entity_id_1='{self.rep_entity_id_1}',
			rep_entity_id_2_type='{self.rep_entity_id_2_type}',
			rep_entity_id_2='{self.rep_entity_id_2}',
			rep_entity_id_3_type='{self.rep_entity_id_3_type}',
			rep_entity_id_3='{self.rep_entity_id_3}',
			rep_entity_id_4_type='{self.rep_entity_id_4_type}',
			rep_entity_id_4='{self.rep_entity_id_4}',
			)>"""
		)
