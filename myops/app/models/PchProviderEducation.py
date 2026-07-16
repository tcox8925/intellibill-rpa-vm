from sqlalchemy import Column, DateTime
from sqlalchemy.dialects.mssql import NVARCHAR
from app.models.BaseClasses import Base


class Pch_Provider_Education(Base):
    __tablename__ = "pch_provider_education"
    __table_args__ = {"schema": "wpo"}

    txn_id = Column(NVARCHAR(None), primary_key=True, nullable=True)
    school_program_name = Column(NVARCHAR(None), nullable=True)
    type = Column(NVARCHAR(None), nullable=True)
    specialty = Column(NVARCHAR(None), nullable=True)
    grad_year = Column(NVARCHAR(None), nullable=True)
    location = Column(NVARCHAR(None), nullable=True)
    txn_id_provider = Column(NVARCHAR(None), nullable=True)
    updated_on = Column(DateTime, nullable=True)
    source = Column(NVARCHAR(None), nullable=True)

    def __repr__(self):
        return f"<Pch_Provider_Education(txn_id={self.txn_id}, school_program_name={self.school_program_name}, type={self.type}, grad_year={self.grad_year})>"
