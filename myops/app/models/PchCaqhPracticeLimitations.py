from sqlalchemy import Column, DateTime, NVARCHAR, VARCHAR, Integer
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER, BIT
from app.models.BaseClasses import Base
from uuid import uuid4
from datetime import datetime, timezone


class Pch_Caqh_Practice_Limitations(Base):
    __tablename__ = "pch_caqh_practice_limitations"
    __table_args__ = {"schema": "wpo"}

    txn_id           = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid4, nullable=False)
    txn_id_provider  = Column(UNIQUEIDENTIFIER, nullable=False, index=True)
    npi              = Column(VARCHAR(20), nullable=False, index=True)
    practice_uid     = Column(NVARCHAR(50), nullable=True, index=True)
    practice_id      = Column(NVARCHAR(50), nullable=True)
    age_flag         = Column(BIT, nullable=True)
    age_min          = Column(Integer, nullable=True)
    age_max          = Column(Integer, nullable=True)
    gender_limitation = Column(NVARCHAR(100), nullable=True)
    updated_on       = Column(DateTime, nullable=True, default=lambda: datetime.now(timezone.utc))