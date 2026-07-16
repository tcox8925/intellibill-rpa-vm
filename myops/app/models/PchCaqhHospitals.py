from sqlalchemy import Column, DateTime, NVARCHAR, VARCHAR
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER, BIT
from app.models.BaseClasses import Base
from uuid import uuid4
from datetime import datetime,timezone


class Pch_Caqh_Hospitals(Base):
    __tablename__ = "pch_caqh_hospitals"
    __table_args__ = {"schema": "wpo"}

    txn_id            = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid4, nullable=False)
    txn_id_provider   = Column(UNIQUEIDENTIFIER, nullable=False, index=True)
    npi               = Column(VARCHAR(20), nullable=False, index=True)
    hospital_name     = Column(NVARCHAR(255), nullable=True)
    aha_id            = Column(NVARCHAR(50), nullable=True)
    privileges        = Column(NVARCHAR(200), nullable=True)
    staff_category    = Column(NVARCHAR(200), nullable=True)
    unrestricted_flag = Column(BIT, nullable=True)
    start_date        = Column(VARCHAR(10), nullable=True)   # stored as string per DDL
    end_date          = Column(VARCHAR(10), nullable=True)   # stored as string per DDL
    updated_on        = Column(DateTime, nullable=True, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return (
            f"<Pch_Caqh_Hospitals(txn_id={self.txn_id}, "
            f"txn_id_provider={self.txn_id_provider}, npi={self.npi}, "
            f"hospital_name={self.hospital_name})>"
        )