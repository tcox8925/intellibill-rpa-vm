from sqlalchemy import Column, DateTime, NVARCHAR, VARCHAR
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER, BIT
from app.models.BaseClasses import Base
from uuid import uuid4
from datetime import datetime, timezone


class Pch_Caqh_Practice_Accessibility(Base):
    __tablename__ = "pch_caqh_practice_accessibility"
    __table_args__ = {"schema": "wpo"}

    txn_id                        = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid4, nullable=False)
    txn_id_provider               = Column(UNIQUEIDENTIFIER, nullable=False, index=True)
    npi                           = Column(VARCHAR(20), nullable=False, index=True)
    practice_uid                  = Column(NVARCHAR(50), nullable=True, index=True)
    practice_id                   = Column(NVARCHAR(50), nullable=True)
    accessibility                 = Column(NVARCHAR(200), nullable=True)
    accessibility_flag            = Column(BIT, nullable=True)
    other_accessibility_description = Column(NVARCHAR(500), nullable=True)
    updated_on                    = Column(DateTime, nullable=True, default=lambda: datetime.now(timezone.utc))