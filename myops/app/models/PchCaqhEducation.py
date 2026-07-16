from sqlalchemy import Column, Date, DateTime, NVARCHAR, VARCHAR
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
from app.models.BaseClasses import Base
from uuid import uuid4
from datetime import datetime, timezone


class Pch_Caqh_Education(Base):
    __tablename__ = "pch_caqh_education"
    __table_args__ = {"schema": "wpo"}

    txn_id          = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid4, nullable=False)
    txn_id_provider = Column(UNIQUEIDENTIFIER, nullable=False, index=True)
    npi             = Column(VARCHAR(20), nullable=False, index=True)
    program_name    = Column(NVARCHAR(300), nullable=True)
    type            = Column(NVARCHAR(100), nullable=True)
    specialty       = Column(NVARCHAR(200), nullable=True)
    grad_year       = Column(VARCHAR(10), nullable=True)
    start_date      = Column(Date, nullable=True)
    end_date        = Column(Date, nullable=True)
    location_city   = Column(NVARCHAR(200), nullable=True)
    location_state  = Column(NVARCHAR(50), nullable=True)
    country         = Column(NVARCHAR(200), nullable=True)
    degree_abbreviation = Column(NVARCHAR(50), nullable=True)
    updated_on      = Column(DateTime, nullable=True, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return (
            f"<Pch_Caqh_Education(txn_id={self.txn_id}, "
            f"txn_id_provider={self.txn_id_provider}, npi={self.npi}, "
            f"type={self.type}, program_name={self.program_name})>"
        )