from app.models.BaseClasses import Base
from sqlalchemy import Column, String
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
import uuid


class CommissionItem(Base):
    __tablename__ = "com_items"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4, nullable=False)
    job_id = Column(String)
    txn_id_com_header = Column(String)
    company_id = Column(String)
    company_name = Column(String)
    carrier_id = Column(String)
    carrier_name = Column(String)
    npn = Column(String)
    agent_name = Column(String)
    agility_id = Column(String)
    associated_statement = Column(String)
    writing_agent_npn = Column(String)
    writing_agent = Column(String)
    payment_schedule = Column(String)
    payment = Column(String)
    payment_type = Column(String)
    policy_state = Column(String)
    effective_date = Column(String)
    coverage_month = Column(String)
    market = Column(String)
    insured_name = Column(String)
    months = Column(String)
    plan = Column(String)
    lives = Column(String)
    premium = Column(String)
    split = Column(String)
    first_year_renewal = Column(String)
    account_number = Column(String)
    memo = Column(String)
    statement_month = Column(String)
    report_date = Column(String)
    load_date = Column(String)
    raw_file_name = Column(String)
    source = Column(String)

    def __repr__(self):
        values = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        return f"<CommissionItem {values}>"