from sqlalchemy import Column, Date, DateTime, NVARCHAR, VARCHAR
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER, BIT
from app.models.BaseClasses import Base
from uuid import uuid4
from datetime import datetime, timezone


class Pch_Caqh_Insurance(Base):
    __tablename__ = "pch_caqh_insurance"
    __table_args__ = {"schema": "wpo"}

    txn_id          = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid4, nullable=False)
    txn_id_provider = Column(UNIQUEIDENTIFIER, nullable=False, index=True)
    npi             = Column(VARCHAR(20), nullable=False, index=True)
    carrier_name    = Column(NVARCHAR(300), nullable=True)
    policy_number   = Column(NVARCHAR(100), nullable=True)
    insurance_type  = Column(NVARCHAR(100), nullable=True)
    start_date      = Column(Date, nullable=True)
    end_date        = Column(Date, nullable=True)
    occurrence      = Column(NVARCHAR(100), nullable=True)
    aggregate       = Column(NVARCHAR(100), nullable=True)
    self_insured    = Column(BIT, nullable=True)
    updated_on      = Column(DateTime, nullable=True, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return (
            f"<Pch_Caqh_Insurance(txn_id={self.txn_id}, "
            f"txn_id_provider={self.txn_id_provider}, npi={self.npi}, "
            f"carrier_name={self.carrier_name})>"
        )