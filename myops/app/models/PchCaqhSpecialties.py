from sqlalchemy import Column, Date, DateTime, NVARCHAR, VARCHAR
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER, BIT
from app.models.BaseClasses import Base
from uuid import uuid4
from datetime import datetime, timezone


class Pch_Caqh_Specialties(Base):
    __tablename__ = "pch_caqh_specialties"
    __table_args__ = {"schema": "wpo"}

    txn_id              = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid4, nullable=False)
    txn_id_provider     = Column(UNIQUEIDENTIFIER, nullable=False, index=True)
    npi                 = Column(VARCHAR(20), nullable=False, index=True)
    specialty_name      = Column(NVARCHAR(200), nullable=True)
    taxonomy_code       = Column(VARCHAR(50), nullable=True)
    board_name          = Column(NVARCHAR(200), nullable=True)
    certification_date  = Column(Date, nullable=True)
    expiration_date     = Column(Date, nullable=True)
    board_certified_flag = Column(BIT, nullable=True)
    updated_on          = Column(DateTime, nullable=True, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return (
            f"<Pch_Caqh_Specialties(txn_id={self.txn_id}, "
            f"txn_id_provider={self.txn_id_provider}, npi={self.npi}, "
            f"specialty_name={self.specialty_name})>"
        )