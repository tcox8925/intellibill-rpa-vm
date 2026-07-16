from sqlalchemy import Column, DateTime, NVARCHAR, VARCHAR
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER, BIT
from app.models.BaseClasses import Base
from uuid import uuid4
from datetime import datetime, timezone


class Pch_Caqh_Disclosures(Base):
    __tablename__ = "pch_caqh_disclosures"
    __table_args__ = {"schema": "wpo"}

    txn_id          = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid4, nullable=False)
    txn_id_provider = Column(UNIQUEIDENTIFIER, nullable=False, index=True)
    npi             = Column(VARCHAR(20), nullable=False, index=True)
    disclosure_id   = Column(NVARCHAR(50), nullable=True)
    question_summary = Column(NVARCHAR(500), nullable=True)
    answer_flag     = Column(BIT, nullable=True)
    explanation     = Column(NVARCHAR(None), nullable=True)  # NVARCHAR(max) in SQLAlchemy
    updated_on      = Column(DateTime, nullable=True, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return (
            f"<Pch_Caqh_Disclosures(txn_id={self.txn_id}, "
            f"txn_id_provider={self.txn_id_provider}, npi={self.npi}, "
            f"disclosure_id={self.disclosure_id})>"
        )