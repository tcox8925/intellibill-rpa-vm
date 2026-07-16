from sqlalchemy import Column, DateTime
from sqlalchemy.dialects.mssql import NVARCHAR
from app.models.BaseClasses import Base


class Pch_Provider_Identifiers(Base):
    __tablename__ = "pch_provider_identifiers"
    __table_args__ = {"schema": "wpo"}

    txn_id = Column(NVARCHAR(None), primary_key=True, nullable=True)
    status = Column(NVARCHAR(None), nullable=True)
    id_type = Column(NVARCHAR(None), nullable=True)
    id_issuer = Column(NVARCHAR(None), nullable=True)
    id_type_value = Column(NVARCHAR(None), nullable=True)
    id_description = Column(NVARCHAR(None), nullable=True)
    id_issue_date = Column(NVARCHAR(None), nullable=True)
    id_state = Column(NVARCHAR(None), nullable=True)
    txn_id_provider = Column(NVARCHAR(None), nullable=True)
    updated_on = Column(DateTime, nullable=True)
    source = Column(NVARCHAR(None), nullable=True)

    def __repr__(self):
        return f"<Pch_Provider_Identifiers(txn_id={self.txn_id}, id_type={self.id_type}, id_value={self.id_type_value}, status={self.status})>"
