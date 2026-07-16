from sqlalchemy import Column, Integer, String, DateTime, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from app.models.BaseClasses import Base

class Pch_Sales_Service(Base):
    __tablename__ = "lup_pch_sales_service"
    __table_args__ = {"schema": "wpo"}

    id = Column(Integer, primary_key=True, autoincrement=True)
    txn_id = Column(String(36), nullable=False, server_default=func.newid())
    service_status = Column(String(50), nullable=False)
    service_name = Column(String(50), nullable=False)
    service_type = Column(String(50), nullable=False)
    service_description = Column(String(4000), nullable=True)
    service_rate_type = Column(String(10), nullable=True)
    service_rate = Column(Numeric(19, 4), nullable=True)
    service_price_desc = Column(String(1000), nullable=True)
    created_on = Column(DateTime, nullable=False, server_default=func.getutcdate())
    updated_on = Column(DateTime, nullable=True)

    def __repr__(self):
        return (
            f"<Pch_Sales_Service("
            f"id={self.id}, "
            f"txn_id='{self.txn_id}', "
            f"service_status='{self.service_status}', "
            f"service_name='{self.service_name}', "
            f"service_type='{self.service_type}', "
            f"service_description='{self.service_description}', "
            f"service_rate_type='{self.service_rate_type}', "
            f"service_rate={self.service_rate}, "
            f"service_price_desc='{self.service_price_desc}', "
            f"created_on='{self.created_on}', "
            f"updated_on='{self.updated_on}'"
            f")>"
        )