import uuid
from sqlalchemy import Column, Text
from app.models.BaseClasses import Base
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER

class PchICD10Mapping(Base):
    __tablename__ = "pch_icd10_mapping"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    code = Column(Text)
    short_description_valid_icd_10_fy2025 = Column(Text, nullable=True)
    long_description_valid_icd_10_fy2025 = Column(Text, nullable=True)
    nf_excl = Column(Text, nullable=True)
    
    def __repr__(self):
        values = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        return f"<PchICD10Mapping {values}>"
