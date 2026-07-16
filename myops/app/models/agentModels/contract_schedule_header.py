import uuid
from sqlalchemy import Column, String
from app.models.BaseClasses import Base
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
from pydantic import BaseModel, field_validator


class ContractScheduleHeader(Base):
    __tablename__ = "com_sched_header"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    

    id = Column(String, nullable=True)
    company_id = Column(String, nullable=True)
    company_name = Column(String, nullable=True)
    carrier_id = Column(String, nullable=True)
    carrier_name = Column(String, nullable=True)
    seq_id = Column(String, nullable=True)
    or_schedule_id = Column(String, nullable=True)
    plan_year = Column(String, nullable=True)
    payment_type = Column(String, nullable=True)
    or_system = Column(String, nullable=True)
    product_name = Column(String, nullable=True)
    level_cat = Column(String, nullable=True)
    load_date = Column(String, nullable=True)


class ContractScheduleHeaderCreateUpdate(BaseModel):
    id: str  # "0" → create, otherwise update
    company_id: str
    company_name: str
    plan_year: str
    payment_type: str
    product_name: str
    level_cat: str
    carrier_id: str
    carrier_name: str

    @field_validator("id")
    def validate_id(cls, v):
        if not v:
            return "0"
        return v.strip()

    class Config:
        from_attributes = True