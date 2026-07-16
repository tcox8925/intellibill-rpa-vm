from sqlalchemy import Column, Date
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER, NVARCHAR, DATETIME2
from app.models.BaseClasses import Base


class Pch_Regulatory_Fail_Details(Base):
    __tablename__ = "pch_regulatory_fail_details"
    __table_args__ = {"schema": "wpo"}

    txn_id = Column(UNIQUEIDENTIFIER, primary_key=True, nullable=False)
    txn_id_reg = Column(UNIQUEIDENTIFIER, nullable=False)
    txn_id_provider = Column(UNIQUEIDENTIFIER, nullable=False)
    source = Column(NVARCHAR(100), nullable=True)
    check_type = Column(NVARCHAR(50), nullable=True)
    action_date = Column(Date, nullable=True)
    description = Column(NVARCHAR(None), nullable=True)  # NVARCHAR(MAX)
    created_on = Column(DATETIME2(3), nullable=True, default='sysutcdatetime()')

    def __repr__(self):
        return (
            f"<Pch_Regulatory_Fail_Details(txn_id={self.txn_id}, "
            f"txn_id_reg={self.txn_id_reg}, txn_id_provider={self.txn_id_provider}, "
            f"check_type={self.check_type}, source={self.source})>"
        )