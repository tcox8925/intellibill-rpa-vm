from sqlalchemy import Column, NVARCHAR, DateTime, BigInteger
from sqlalchemy.dialects.mssql import BIT
from app.models.BaseClasses import Base

class pch_carrier_contracting(Base):
    __tablename__ = "pch_carrier_contracting"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(BigInteger, primary_key=True, autoincrement=True, nullable=False)
    txn_id = Column(NVARCHAR(50), nullable=False)
    txn_id_provider = Column(NVARCHAR(50), nullable=True)
    status = Column(NVARCHAR(25), nullable=True)
    carrier_id = Column(NVARCHAR(50), nullable=True)
    carrier_name = Column(NVARCHAR(100), nullable=True)
    carrier_product = Column(NVARCHAR(100), nullable=True)
    carrier_status = Column(NVARCHAR(25), nullable=True)
    is_credentialing = Column(BIT, nullable=True)
    created_on = Column(DateTime, nullable=False)
    updated_on = Column(DateTime, nullable=True)

    def __repr__(self):
        return (
            f"<PchCarrierContracting("
            f"pk_id={self.pk_id}, "
            f"txn_id='{self.txn_id}', "
            f"txn_id_provider='{self.txn_id_provider}', "
            f"status='{self.status}', "
            f"carrier_id='{self.carrier_id}', "
            f"carrier_name='{self.carrier_name}', "
            f"carrier_product='{self.carrier_product}', "
            f"carrier_status='{self.carrier_status}', "
            f"is_credentialing='{self.is_credentialing}', "
            f"created_on='{self.created_on}', "
            f"updated_on='{self.updated_on}'"
            f")>"
        )