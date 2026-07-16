from sqlalchemy import Column, BigInteger, DateTime
from sqlalchemy.dialects.mssql import VARCHAR, NVARCHAR
from sqlalchemy.sql import func
from app.models.BaseClasses import Base  # adjust import as per your project

class Ops_Pch_Logs(Base):
    __tablename__ = "ops_pch_logs"
    __table_args__ = {"schema": "wpo"}

    log_id = Column(BigInteger, primary_key=True, autoincrement=True)         # IDENTITY(1,1)
    txn_id = Column(VARCHAR(50), nullable=False)
    script_name = Column(VARCHAR(100), nullable=False)
    process_type = Column(VARCHAR(100), nullable=False)
    status = Column(VARCHAR(50), nullable=False)
    error = Column(NVARCHAR(4000), nullable=True)
    company_id = Column(VARCHAR(50), nullable=True)
    carrier_id = Column(VARCHAR(50), nullable=True)
    file_path = Column(NVARCHAR(500), nullable=True)
    started_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=True, server_default=func.now())

    def __repr__(self):
        return (
            f"<OpsPchLogs(log_id={self.log_id}, txn_id={self.txn_id}, "
            f"script_name={self.script_name}, status={self.status})>"
        )