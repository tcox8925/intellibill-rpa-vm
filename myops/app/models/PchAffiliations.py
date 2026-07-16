from sqlalchemy import Column, DateTime
from sqlalchemy.dialects.mssql import NVARCHAR, INTEGER
from app.models.BaseClasses import Base


class Pch_Affiliations(Base):
    __tablename__ = "pch_affiliations"
    __table_args__ = {"schema": "wpo"}

    txn_id = Column(NVARCHAR(None), primary_key=True, nullable=True)
    affiliate_name = Column(NVARCHAR(None), nullable=True)
    location = Column(NVARCHAR(None), nullable=True)
    txn_id_provider = Column(NVARCHAR(None), nullable=True)
    updated_on = Column(DateTime, nullable=True)
    source = Column(NVARCHAR(None), nullable=True)

    def __repr__(self):
        return f"<Pch_Affiliations(id={self.id}, affiliate_name={self.affiliate_name}, location={self.location})>"
