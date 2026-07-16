from app.models.BaseClasses import Base
from sqlalchemy import Column, String, text
from sqlalchemy.dialects.postgresql import UUID


class CommissionCalcsHistory(Base):
    __tablename__ = "com_calcs_history"
    __table_args__ = {"schema": "wpo"}

    job_id = Column(String(1000))
    txn_status = Column(String(50))
    txn_id_com_header = Column(String(50))
    company_id = Column(String(50))
    company_name = Column(String(1000))
    carrier_id = Column(String(50))
    carrier_name = Column(String(255))
    acct = Column(String(50))
    statement_date = Column(String(100))
    market = Column(String(255))
    lives = Column(String(100))
    payable = Column(String(100))
    agent_npn = Column(String(50))
    agent_writing_number = Column(String(50))
    agent_name = Column(String(1000))
    policy_eff_date = Column(String(100))
    coverage_month = Column(String(100))
    split = Column(String(50))
    premium = Column(String(100))
    num_days = Column(String(100))
    num_months = Column(String(100))
    agility_override_schedule = Column(String(1000))
    agility_override_calc_method = Column(String(1000))
    comm_rate = Column(String(100))
    comm_amt = Column(String(100))
    override_rate = Column(String(100))
    override_amt = Column(String(100))
    agent_level = Column(String(50))
    agent_id = Column(String(50))
    agent_rate = Column(String(100))
    agent_pay = Column(String(100))
    agent_type = Column(String(50))
    agent_statement_no = Column(String(1000))
    a_npn = Column(String(50))
    assignee_name = Column(String(1000))
    a_id = Column(String(50))
    a_rate = Column(String(100))
    a_pay = Column(String(100))
    a_type = Column(String(1000))
    a_statement_no = Column(String(1000))
    recruiter = Column(String(1000))
    policy_state = Column(String(50))
    payment_plan = Column(String(255))
    sales_director_contract = Column(String(1000))
    agility_or = Column(String(255))
    calc_total = Column(String(100))
    assignor_as_upline = Column(String(50))
    agent_assignee_ra_same_sign = Column(String(50))
    first_year_renewal = Column(String(50))
    insured_name = Column(String(255))
    memo = Column(String(4000))
    all_good_flag = Column(String(10))
    report_date = Column(String(100))
    load_date = Column(String(100))
    raw_file_name = Column(String(1000))

    pk_id = Column(
        UUID(as_uuid=True),
        server_default=text("gen_random_uuid()"),
        nullable=True,
        primary_key=True,
    )

    def __repr__(self):
        values = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        return f"<CommissionCalcsHistory {values}>"