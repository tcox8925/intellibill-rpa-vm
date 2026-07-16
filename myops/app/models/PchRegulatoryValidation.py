from sqlalchemy import Column
from sqlalchemy.dialects.mssql import NVARCHAR
from app.models.BaseClasses import Base


class Pch_Regulatory_Validation(Base):
    __tablename__ = "pch_regulatory_validation"
    __table_args__ = {"schema": "wpo"}

    txn_id = Column(NVARCHAR(None), primary_key=True, nullable=True)
    audit_id = Column(NVARCHAR(None), nullable=True)
    status = Column(NVARCHAR(None), nullable=True)
    source = Column(NVARCHAR(None), nullable=True)
    date_time = Column(NVARCHAR(None), nullable=True)
    txn_id_provider = Column(NVARCHAR(None), nullable=True)

    def __repr__(self):
        return f"<Pch_Regulatory_Validation(txn_id={self.txn_id}, audit_id={self.audit_id}, status={self.status})>"
