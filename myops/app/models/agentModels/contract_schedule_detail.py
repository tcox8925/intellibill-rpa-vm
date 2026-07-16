from app.models.BaseClasses import Base
from sqlalchemy import Column, String
import uuid
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
from pydantic import BaseModel
from typing import List, Optional


class ContractScheduleDetail(Base):
    __tablename__ = "com_sched_detail"
    __table_args__ = {"schema": "wpo"} 
    
    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)

    id = Column(String, nullable=True)
    company_id = Column(String, nullable=True)
    company_name = Column(String, nullable=True)
    carrier_id = Column(String, nullable=True)
    carrier_name = Column(String, nullable=True)
    or_schedule_id = Column(String, nullable=True)
    seq_id = Column(String, nullable=True)
    or_detail_id = Column(String, nullable=True)
    payment_type = Column(String, nullable=True)
    status = Column(String, nullable=True)
    level_cat = Column(String, nullable=True)
    level = Column(String, nullable=True)
    territory = Column(String, nullable=True)
    rate_type = Column(String, nullable=True)
    rate_value = Column(String, nullable=True)
    base_product = Column(String, nullable=True)
    carrier_base_rate = Column(String, nullable=True)
    agent_base_rate = Column(String, nullable=True)
    agility_base_rate = Column(String, nullable=True)
    rate_type_0 = Column(String, nullable=True)
    rate_value_0 = Column(String, nullable=True)
    rate_type_1 = Column(String, nullable=True)
    rate_value_1 = Column(String, nullable=True)
    rate_type_2 = Column(String, nullable=True)
    rate_value_2 = Column(String, nullable=True)
    load_date = Column(String, nullable=True)
    hdr_id = Column(String, nullable=True)


class ContractScheduleDetailItem(BaseModel):
    id: Optional[str] = "0"
    company_id: Optional[str] = None
    company_name: Optional[str] = None
    carrier_id: Optional[str] = None
    carrier_name: Optional[str] = None
    or_schedule_id: str
    seq_id: Optional[str] = None
    payment_type: Optional[str] = None
    level_cat: Optional[str] = None
    level: Optional[str] = None
    territory: Optional[str] = None
    rate_type: Optional[str] = None
    rate_value: Optional[str] = None
    base_product: Optional[str] = None
    carrier_base_rate: Optional[str] = None
    agent_base_rate: Optional[str] = None
    agility_base_rate: Optional[str] = None
    rate_type_0: Optional[str] = None
    rate_value_0: Optional[str] = None
    rate_type_1: Optional[str] = None
    rate_value_1: Optional[str] = None
    rate_type_2: Optional[str] = None
    rate_value_2: Optional[str] = None

class ContractScheduleDetailBulkRequest(BaseModel):
    details: List[ContractScheduleDetailItem]
