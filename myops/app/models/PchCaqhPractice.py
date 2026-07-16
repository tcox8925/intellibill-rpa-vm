from sqlalchemy import Column, Date, DateTime, NVARCHAR, VARCHAR
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER, BIT
from app.models.BaseClasses import Base
from uuid import uuid4
from datetime import datetime, timezone


class Pch_Caqh_Practice(Base):
    __tablename__ = "pch_caqh_practice"
    __table_args__ = {"schema": "wpo"}

    txn_id                  = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid4, nullable=False)
    txn_id_provider         = Column(UNIQUEIDENTIFIER, nullable=False, index=True)
    npi                     = Column(VARCHAR(20), nullable=False, index=True)
    practice_uid            = Column(NVARCHAR(50), nullable=True, index=True)
    practice_id             = Column(NVARCHAR(50), nullable=True)
    practice_name           = Column(NVARCHAR(300), nullable=True)
    address                 = Column(NVARCHAR(300), nullable=True)
    city                    = Column(NVARCHAR(200), nullable=True)
    state                   = Column(NVARCHAR(50), nullable=True)
    zip                     = Column(NVARCHAR(20), nullable=True)
    phone                   = Column(NVARCHAR(50), nullable=True)
    fax                     = Column(NVARCHAR(50), nullable=True)
    after_hours_phone       = Column(NVARCHAR(50), nullable=True)
    currently_practicing_flag = Column(BIT, nullable=True)
    ada_flag                = Column(BIT, nullable=True)
    interpreter_flag        = Column(BIT, nullable=True)
    practice_type           = Column(NVARCHAR(200), nullable=True)
    start_date              = Column(Date, nullable=True)
    end_date                = Column(Date, nullable=True)
    list_in_directory_flag  = Column(BIT, nullable=True)
    electronic_billing_flag = Column(BIT, nullable=True)
    primary_flag            = Column(BIT, nullable=True)
    updated_on              = Column(DateTime, nullable=True, default=lambda: datetime.now(timezone.utc))