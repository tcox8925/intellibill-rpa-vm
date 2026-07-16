from app.models.BaseClasses import Base
from sqlalchemy.orm import relationship
import uuid
from sqlalchemy import Column, String, BigInteger, ForeignKey, Text
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER

class Sub_Entity(Base):
	__tablename__ = 'sub_entity'
	__schema__ = "ops_sec"
	__table_args__ = {'schema': __schema__}

	# Foreign key to Entity (BIGINT, not the GUID id)
	entity_id = Column(
		Text,
		ForeignKey(f"{__schema__}.entity.entity_id"),
		nullable=False
	)

	sub_entity_lname = Column(String(100), nullable=True)
	sub_entity_fname = Column(String(100), nullable=True)
	sub_entity_address_1 = Column(String(200), nullable=True)
	sub_entity_address_2 = Column(String(200), nullable=True)
	sub_entity_city = Column(String(100), nullable=True)
	sub_entity_state = Column(String(100), nullable=True)
	sub_entity_zip = Column(String(20), nullable=True)

	sub_entity_id_1_type = Column(String(50), nullable=True)
	sub_entity_id_1 = Column(String(100), nullable=True)
	sub_entity_id_2_type = Column(String(50), nullable=True)
	sub_entity_id_2 = Column(String(100), nullable=True)
	sub_entity_id_3_type = Column(String(50), nullable=True)
	sub_entity_id_3 = Column(String(100), nullable=True)
	sub_entity_id_4_type = Column(String(50), nullable=True)
	sub_entity_id_4 = Column(String(100), nullable=True)

	# Primary Key (GUID)
	id = Column(
		UNIQUEIDENTIFIER,
		primary_key=True,
		default=uuid.uuid4,
		nullable=False
	)

	# Unique bigint identifier
	sub_entity_id = Column(Text, unique=True, nullable=False)

	# Relationship back to Entity
	entity = relationship("Entity", back_populates="sub_entities")
	locations = relationship("Entity_Location", back_populates="sub_entity", cascade="all, delete-orphan")
	representatives = relationship("Entity_Representative", back_populates="sub_entity", cascade="all, delete-orphan")

	def __repr__(self):
		return (
			f"""<Sub_Entity(
			id={self.id},
			entity_id={self.entity_id},
			sub_entity_id={self.sub_entity_id}, 
			sub_entity_lname='{self.sub_entity_lname}', 
			sub_entity_fname='{self.sub_entity_fname}',
			sub_entity_address_1='{self.sub_entity_address_1}',
			sub_entity_address_2='{self.sub_entity_address_2}',
			sub_entity_city='{self.sub_entity_city}',
			sub_entity_state='{self.sub_entity_state}',
			sub_entity_zip='{self.sub_entity_zip}',
			sub_entity_id_1_type='{self.sub_entity_id_1_type}',
			sub_entity_id_1='{self.sub_entity_id_1}',
			sub_entity_id_2_type='{self.sub_entity_id_2_type}',
			sub_entity_id_2='{self.sub_entity_id_2}',
			sub_entity_id_3_type='{self.sub_entity_id_3_type}',
			sub_entity_id_3='{self.sub_entity_id_3}',
			sub_entity_id_4_type='{self.sub_entity_id_4_type}',
			sub_entity_id_4='{self.sub_entity_id_4}'
			)>"""
		)