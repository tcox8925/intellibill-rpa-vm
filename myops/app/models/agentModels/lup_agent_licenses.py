import uuid
from sqlalchemy import Column, String, Date
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
from app.models.BaseClasses import Base
from pydantic import BaseModel
from datetime import date
from uuid import UUID
from typing import Optional

class LupAgentLicenses(Base):
    __tablename__ = "lup_agent_licenses"
    __table_args__ = {"schema": "wpo"}

    transaction_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)

    agent_npn = Column(String)
    lic_id = Column(String)
    type = Column(String)
    status = Column(String)
    state = Column(String)
    issue_date = Column(Date)
    expiry_date = Column(Date)
    license_number = Column(String)
    license_owner = Column(String)
    license_market = Column(String)
    certification_year = Column(String)
    npn_valid = Column(String)
    
    def __repr__(self):
        values = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        return f"<LupAgentLicenses {values}>"

class LicenseAddRequest(BaseModel):
    agent_npn: str
    state_code: str
    issue_date: date
    lic_id: str
    expiry_date: Optional[date] = None
    license_number: Optional[str] = None
    license_market: Optional[str] = None
    status: Optional[str] = None

class LicenseUpdateRequest(BaseModel):
    transaction_id: UUID
    state_code: Optional[str] = None
    issue_date: Optional[date] = None
    expiry_date: Optional[date] = None
    # license_number: Optional[str] = None
    license_market: Optional[str] = None
    # status: Optional[str] = None

class CertificationAddRequest(BaseModel):
    agent_npn: str
    certification_name: str
    issue_date: date
    certification_year: str
    npn_valid: str

class CertificationUpdateRequest(BaseModel):
    transaction_id: UUID
    certification_name: str | None = None
    issue_date: date | None = None
    certification_year: str | None = None
    npn_valid: str | None = None

class SBEAddRequest(BaseModel):
    agent_npn: str
    state_code: str
    issue_date: date
    expiry_date: Optional[date] = None
    license_number: Optional[str] = None
    license_market: Optional[str] = None
    certification_year: Optional[str] = None

class SBEUpdateRequest(BaseModel):
    transaction_id: UUID
    state_code: Optional[str] = None
    issue_date: Optional[date] = None
    expiry_date: Optional[date] = None
    license_number: Optional[str] = None
    license_market: Optional[str] = None
    certification_year: Optional[str] = None
