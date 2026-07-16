from sqlalchemy import Column, NVARCHAR, Text
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
from app.models.BaseClasses import Base
import uuid

class Carrier(Base):
    __tablename__ = "lup_carriers"
    __table_args__ = {"schema": "wpo"}

    id = Column(NVARCHAR(None), nullable=True)
    vendor_name = Column(NVARCHAR(None), nullable=True)
    market = Column(NVARCHAR(None), nullable=True)
    state_availability = Column(NVARCHAR(None), nullable=True)
    modified_time = Column(NVARCHAR(None), nullable=True)
    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, nullable=False)

    def __repr__(self):
        return (
            f"<Carrier(pk_id={self.pk_id}, "
            f"id={self.id}, "
            f"vendor_name={self.vendor_name}, "
            f"market={self.market}, "
            f"state_availability={self.state_availability}, "
            f"modified_time={self.modified_time})>"
        )

class CarrierShort(Base):
    __tablename__ = "lup_carriers_short"
    __table_args__ = {"schema": "wpo"}  # schema = wpo
    
    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)

    id = Column(Text, primary_key=True, index=True, nullable=True)
    vendor_name = Column(Text, nullable=True)
    carrier_short_name = Column(Text, nullable=True)
    prefix = Column(Text, nullable=True)
    acu_file_name_prefix = Column(Text, nullable=True)
    bob_file_name_prefix = Column(Text, nullable=True)
    writing_num_flag = Column(Text, nullable=True)
    pch_agreement = Column(Text, nullable=True)
    delegated_cred = Column(Text, nullable=True)
