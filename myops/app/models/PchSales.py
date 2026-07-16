from sqlalchemy import Column, NVARCHAR, DateTime, BigInteger, Numeric
from app.models.BaseClasses import Base

class pch_sales(Base):
    __tablename__ = "pch_sales"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(BigInteger, primary_key=True, autoincrement=True, nullable=False)
    txn_id = Column(NVARCHAR(50), nullable=False)
    txn_id_provider = Column(NVARCHAR(50), nullable=False)
    pch_header_txn = Column(NVARCHAR(50), nullable=True)
    service_status = Column(NVARCHAR(25), nullable=True)
    service_name = Column(NVARCHAR(100), nullable=True)
    service_type = Column(NVARCHAR(50), nullable=True)
    service_description = Column(NVARCHAR(1000), nullable=True)
    service_rate_type = Column(NVARCHAR(50), nullable=True)
    service_rate = Column(Numeric(19, 4), nullable=True)
    service_price_desc = Column(NVARCHAR(500), nullable=True)
    created_on = Column(DateTime, nullable=False)
    updated_on = Column(DateTime, nullable=True)

    def __repr__(self):
        return (
            f"<PchSales("
            f"pk_id={self.pk_id}, "
            f"txn_id='{self.txn_id}', "
            f"txn_id_provider='{self.txn_id_provider}', "
            f"pch_header_txn='{self.pch_header_txn}', "
            f"service_status='{self.service_status}', "
            f"service_name='{self.service_name}', "
            f"service_type='{self.service_type}', "
            f"service_description='{self.service_description}', "
            f"service_rate_type='{self.service_rate_type}', "
            f"service_rate={self.service_rate}, "
            f"service_price_desc='{self.service_price_desc}', "
            f"attachment_doc='{self.attachment_doc}', "
            f"created_on='{self.created_on}', "
            f"updated_on='{self.updated_on}'"
            f")>"
        )