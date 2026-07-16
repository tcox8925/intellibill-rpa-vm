from sqlalchemy import Column, Date, DateTime, NVARCHAR, VARCHAR
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER, BIT
from app.models.BaseClasses import Base
from uuid import uuid4
from datetime import datetime, timezone


class Pch_Caqh_Provider_Info(Base):
    __tablename__ = "pch_caqh_provider_info"
    __table_args__ = {"schema": "wpo"}

    txn_id                    = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid4, nullable=False)
    txn_id_provider           = Column(UNIQUEIDENTIFIER, nullable=False, index=True)
    npi                       = Column(VARCHAR(20), nullable=False, index=True)
    first_name                = Column(NVARCHAR(200), nullable=True)
    last_name                 = Column(NVARCHAR(200), nullable=True)
    gender                    = Column(NVARCHAR(50), nullable=True)
    birth_date                = Column(Date, nullable=True)
    birth_city                = Column(NVARCHAR(200), nullable=True)
    birth_state               = Column(NVARCHAR(50), nullable=True)
    birth_county              = Column(NVARCHAR(200), nullable=True)
    birth_country             = Column(NVARCHAR(200), nullable=True)
    citizenship_status        = Column(NVARCHAR(100), nullable=True)
    email                     = Column(NVARCHAR(200), nullable=True)
    cell_phone                = Column(NVARCHAR(50), nullable=True)
    race_ethnicity_level_1    = Column(NVARCHAR(100), nullable=True)
    race_ethnicity_level_2    = Column(NVARCHAR(100), nullable=True)
    graduate_type             = Column(NVARCHAR(200), nullable=True)
    provider_type             = Column(NVARCHAR(200), nullable=True)
    other_interests           = Column(NVARCHAR(None), nullable=True)  # NVARCHAR(max)
    dea_flag                  = Column(BIT, nullable=True)
    cds_flag                  = Column(BIT, nullable=True)
    upin_flag                 = Column(BIT, nullable=True)
    npi_flag                  = Column(BIT, nullable=True)
    medicare_flag             = Column(BIT, nullable=True)
    medicaid_flag             = Column(BIT, nullable=True)
    fellowship_flag           = Column(BIT, nullable=True)
    secondary_specialty_flag  = Column(BIT, nullable=True)
    hospital_privilege_flag   = Column(BIT, nullable=True)
    military_service_flag     = Column(BIT, nullable=True)
    work_history_gap_flag     = Column(BIT, nullable=True)
    hospital_based_flag       = Column(BIT, nullable=True)
    affiliated_flag           = Column(BIT, nullable=True)
    delegated_flag            = Column(BIT, nullable=True)
    updated_on                = Column(DateTime, nullable=True, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return (
            f"<Pch_Caqh_Provider_Info(txn_id={self.txn_id}, "
            f"txn_id_provider={self.txn_id_provider}, npi={self.npi}, "
            f"first_name={self.first_name})>"
        )