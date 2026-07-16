from app.models.BaseClasses import Base
from sqlalchemy import Column, String
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
import uuid


class LupCertifications(Base):
    __tablename__ = "lup_certifications"
    __table_args__ = {"schema": "wpo"}


    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4, nullable=False)
    
    cert_code = Column(String)        
    description = Column(String)        
    
    def __repr__(self):
        values = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        return f"<LupCertifications {values}>"