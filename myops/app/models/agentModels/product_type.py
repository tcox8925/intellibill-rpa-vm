from sqlalchemy import Column, NVARCHAR
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
from app.models.BaseClasses import Base


class ProductType(Base):
    __tablename__ = "lup_product_type"
    __table_args__ = {"schema": "wpo"}

    company_id = Column(NVARCHAR(None), nullable=True)
    company_name = Column(NVARCHAR(None), nullable=True)
    product_type = Column(NVARCHAR(None), nullable=True)
    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, nullable=False)

    def __repr__(self):
        return (
            f"<ProductType(pk_id={self.pk_id}, "
            f"company_id={self.company_id}, "
            f"company_name={self.company_name}, "
            f"product_type={self.product_type})>"
        )
