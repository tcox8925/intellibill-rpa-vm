from sqlalchemy import Column, DateTime
from sqlalchemy.dialects.mssql import NVARCHAR, INTEGER
from app.models.BaseClasses import Base


class Pch_Provider_Location(Base):
    __tablename__ = "pch_provider_location"
    __table_args__ = {"schema": "wpo"}

    txn_id = Column(NVARCHAR(None), primary_key=True, nullable=True)
    source = Column(NVARCHAR(None), nullable=True)
    type = Column(NVARCHAR(None), nullable=True)
    location_name = Column(NVARCHAR(None), nullable=True)
    contact = Column(NVARCHAR(None), nullable=True)
    fax = Column(NVARCHAR(None), nullable=True)
    address_1 = Column(NVARCHAR(None), nullable=True)
    address_2 = Column(NVARCHAR(None), nullable=True)
    city = Column(NVARCHAR(None), nullable=True)
    state = Column(NVARCHAR(None), nullable=True)
    zip = Column(NVARCHAR(None), nullable=True)
    txn_id_provider = Column(NVARCHAR(None), nullable=True)
    updated_on = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<Pch_Provider_Location( location_name={self.location_name}, city={self.city}, state={self.state})>"