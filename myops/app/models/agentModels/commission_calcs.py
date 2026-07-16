from app.models.BaseClasses import Base
from sqlalchemy import Column, String
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
import uuid


class CommissionCalcs(Base):
    __tablename__ = "com_calcs"
    __table_args__ = {"schema": "wpo"}


    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4, nullable=False)
    
    job_id = Column(String)
    txn_status = Column(String)
    txn_id_com_header = Column(String)
    company_id = Column(String)
    company_name = Column(String)
    carrier_id = Column(String)
    carrier_name = Column(String)
    acct = Column(String)
    statement_date = Column(String)
    market = Column(String)
    lives = Column(String)
    payable = Column(String)
    agent_npn = Column(String)
    agent_writing_number = Column(String)
    agent_name = Column(String)
    policy_eff_date = Column(String)
    coverage_month = Column(String)
    split = Column(String)
    premium = Column(String)
    num_days = Column(String)
    num_months = Column(String)
    agility_override_schedule = Column(String)
    agility_override_calc_method = Column(String)
    comm_rate = Column(String)
    comm_amt = Column(String)
    override_rate = Column(String)
    override_amt = Column(String)
    agent_level = Column(String)
    agent_id = Column(String)
    agent_rate = Column(String)
    agent_pay = Column(String)
    agent_type = Column(String)
    agent_statement_no = Column(String)
    a_npn = Column(String)
    assignee_name = Column(String)
    a_id = Column(String)   
    a_rate = Column(String)
    a_pay = Column(String)
    a_type = Column(String)
    a_statement_no = Column(String)
    recruiter = Column(String)
    policy_state = Column(String)
    payment_plan = Column(String)
    sales_director_contract = Column(String)
    agility_or = Column(String)
    calc_total = Column(String)
    assignor_as_upline = Column(String)
    agent_assignee_ra_same_sign = Column(String)
    first_year_renewal = Column(String)
    insured_name = Column(String)
    memo = Column(String)
    all_good_flag = Column(String)
    report_date = Column(String)
    load_date = Column(String)
    raw_file_name = Column(String)
        
    
    def __repr__(self):
        values = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        return f"<CommissionCalcs {values}>"