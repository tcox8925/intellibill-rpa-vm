from sqlalchemy import Column, Date, DateTime, NVARCHAR, VARCHAR, Integer
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER, BIT
from app.models.BaseClasses import Base
from uuid import uuid4
from datetime import datetime, timezone


class Pch_Caqh_Malpractice_Claims(Base):
    __tablename__ = "pch_caqh_malpractice_claims"
    __table_args__ = {"schema": "wpo"}

    txn_id                  = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid4, nullable=False)
    txn_id_provider         = Column(UNIQUEIDENTIFIER, nullable=False, index=True)
    npi                     = Column(VARCHAR(20), nullable=False, index=True)
    disclosure_id           = Column(NVARCHAR(50), nullable=True)
    question_summary        = Column(NVARCHAR(500), nullable=True)
    carrier_name            = Column(NVARCHAR(300), nullable=True)
    policy_number           = Column(NVARCHAR(100), nullable=True)
    occurrence_date         = Column(Date, nullable=True)
    claim_date              = Column(Date, nullable=True)
    allegation              = Column(NVARCHAR(None), nullable=True)  # NVARCHAR(max)
    primary_defendant_flag  = Column(BIT, nullable=True)
    num_other_codefendant   = Column(Integer, nullable=True)
    case_involvement        = Column(NVARCHAR(500), nullable=True)
    patient_injury_description = Column(NVARCHAR(None), nullable=True)  # NVARCHAR(max)
    npdb_case_flag          = Column(BIT, nullable=True)
    patient_died_flag       = Column(BIT, nullable=True)
    claim_status            = Column(NVARCHAR(100), nullable=True)
    address1                = Column(NVARCHAR(300), nullable=True)
    address2                = Column(NVARCHAR(300), nullable=True)
    city                    = Column(NVARCHAR(200), nullable=True)
    state                   = Column(NVARCHAR(50), nullable=True)
    zip                     = Column(NVARCHAR(20), nullable=True)
    phone                   = Column(NVARCHAR(50), nullable=True)
    updated_on              = Column(DateTime, nullable=True, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return (
            f"<Pch_Caqh_Malpractice_Claims(txn_id={self.txn_id}, "
            f"txn_id_provider={self.txn_id_provider}, npi={self.npi}, "
            f"disclosure_id={self.disclosure_id})>"
        )