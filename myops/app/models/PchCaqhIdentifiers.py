from sqlalchemy import Column, Date, DateTime, NVARCHAR, VARCHAR
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
from app.models.BaseClasses import Base
from uuid import uuid4
from datetime import datetime, timezone


class Pch_Caqh_Identifiers(Base):
    __tablename__ = "pch_caqh_identifiers"
    __table_args__ = {"schema": "wpo"}

    txn_id          = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid4, nullable=False)
    txn_id_provider = Column(UNIQUEIDENTIFIER, nullable=False, index=True)
    npi             = Column(VARCHAR(20), nullable=False, index=True)
    id_type         = Column(NVARCHAR(100), nullable=True)
    id_value        = Column(NVARCHAR(100), nullable=True)
    state           = Column(NVARCHAR(50), nullable=True)
    issue_date      = Column(Date, nullable=True)
    expiration_date = Column(Date, nullable=True)
    updated_on      = Column(DateTime, nullable=True, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return (
            f"<Pch_Caqh_Identifiers(txn_id={self.txn_id}, "
            f"txn_id_provider={self.txn_id_provider}, npi={self.npi}, "
            f"id_type={self.id_type})>"
        )