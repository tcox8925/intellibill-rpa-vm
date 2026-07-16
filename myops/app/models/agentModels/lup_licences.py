from app.models.BaseClasses import Base
from sqlalchemy import Column, String
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
import uuid


class LupLicenses(Base):
    __tablename__ = "lup_licences"
    __table_args__ = {"schema": "wpo"}


    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4, nullable=False)
    
    state_code = Column(String)
    state_name = Column(String)
        
    
    def __repr__(self):
        values = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        return f"<LupLicenses {values}>"