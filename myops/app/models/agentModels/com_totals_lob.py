import uuid
from sqlalchemy import Column, String, Float, DateTime
from app.models.BaseClasses import Base 
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER


class ComTotalsLob(Base):
    __tablename__ = "com_totals_lob"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4, nullable=False)
    job_id = Column(String(100), nullable=False)
    company_id = Column(String(100))
    company_name = Column(String(255))
    carrier_id = Column(String(100))
    carrier_name = Column(String(255))
    statement_month = Column(String(50))
    npn = Column(String(50))
    agent_name = Column(String(255))
    market = Column(String(100))
    lob_abbv = Column(String(50))
    lob_description = Column(String(255))
    payment_type = Column(String(100))
    associated_statement = Column(String(255))
    statement_total = Column(Float)
    status = Column(String(50))
    status_date = Column(DateTime)
    report_date = Column(DateTime)
    load_date = Column(DateTime)

    def __repr__(self):
        values = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        return f"<ComTotalsLob {values}>"