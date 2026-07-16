from app.models.BaseClasses import Base
from sqlalchemy import Column, String, Text, CHAR
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
import uuid


class LupSBELicenses(Base):
    __tablename__ = "lup_sbe_licenses"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4, nullable=False)
    state_code = Column(CHAR(3), nullable=False)
    name = Column(String(200), nullable=False)
    state_name = Column(String(100), nullable=True)
    file_url = Column(Text, nullable=True)
    site_link = Column(Text, nullable=True)
    qualification = Column(String(50), nullable=True)

    def __repr__(self):
        return f"<LupSBELicenses(pk_id={self.pk_id}, state_code='{self.state_code}', license_name='{self.license_name}')>"
