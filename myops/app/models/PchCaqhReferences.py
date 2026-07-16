from sqlalchemy import Column, DateTime, NVARCHAR, VARCHAR
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
from app.models.BaseClasses import Base
from uuid import uuid4
from datetime import datetime, timezone


class Pch_Caqh_References(Base):
    __tablename__ = "pch_caqh_references"
    __table_args__ = {"schema": "wpo"}

    txn_id          = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid4, nullable=False)
    txn_id_provider = Column(UNIQUEIDENTIFIER, nullable=False, index=True)
    npi             = Column(VARCHAR(20), nullable=False, index=True)
    first_name      = Column(NVARCHAR(200), nullable=True)
    last_name       = Column(NVARCHAR(200), nullable=True)
    relationship    = Column(NVARCHAR(200), nullable=True)
    email           = Column(NVARCHAR(200), nullable=True)
    phone           = Column(NVARCHAR(50), nullable=True)
    updated_on      = Column(DateTime, nullable=True, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return (
            f"<Pch_Caqh_References(txn_id={self.txn_id}, "
            f"txn_id_provider={self.txn_id_provider}, npi={self.npi}, "
            f"last_name={self.last_name})>"
        )