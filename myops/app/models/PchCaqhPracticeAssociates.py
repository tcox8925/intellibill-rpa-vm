from sqlalchemy import Column, DateTime, NVARCHAR, VARCHAR
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER, BIT
from app.models.BaseClasses import Base
from uuid import uuid4
from datetime import datetime, timezone


class Pch_Caqh_Practice_Associates(Base):
    __tablename__ = "pch_caqh_practice_associates"
    __table_args__ = {"schema": "wpo"}

    txn_id            = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid4, nullable=False)
    txn_id_provider   = Column(UNIQUEIDENTIFIER, nullable=False, index=True)
    npi               = Column(VARCHAR(20), nullable=False, index=True)
    practice_uid      = Column(NVARCHAR(50), nullable=True, index=True)
    practice_id       = Column(NVARCHAR(50), nullable=True)
    first_name        = Column(NVARCHAR(200), nullable=True)
    last_name         = Column(NVARCHAR(200), nullable=True)
    middle_initial    = Column(NVARCHAR(10), nullable=True)
    relationship      = Column(NVARCHAR(200), nullable=True)
    email             = Column(NVARCHAR(200), nullable=True)
    phone             = Column(NVARCHAR(50), nullable=True)
    fax               = Column(NVARCHAR(50), nullable=True)
    license_number    = Column(NVARCHAR(100), nullable=True)
    license_state     = Column(NVARCHAR(50), nullable=True)
    updated_on        = Column(DateTime, nullable=True, default=lambda: datetime.now(timezone.utc))