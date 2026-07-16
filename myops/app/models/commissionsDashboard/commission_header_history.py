from app.models.BaseClasses import Base
from sqlalchemy import Column, String, text
from sqlalchemy.dialects.postgresql import UUID


class CommissionHeaderHistory(Base):
    __tablename__ = "com_header_history"
    __table_args__ = {"schema": "wpo"}

    job_id = Column(String(1000))
    txn_status = Column(String(50))
    txn_id = Column(String(50))
    company_id = Column(String(100))
    company_name = Column(String(1000))
    carrier_id = Column(String(100))
    carrier_name = Column(String(255))
    car_prod_name = Column(String(4000))
    car_prod_fname = Column(String(4000))
    car_prod_lname = Column(String(4000))
    car_prod_npn = Column(String(50))
    car_prod_writing_num = Column(String(50))
    car_statement_date = Column(String(100))
    car_commission_type = Column(String(50))
    car_policy_id = Column(String(50))
    car_policy_live_cnt = Column(String(50))
    car_com_split_rate = Column(String(100))
    car_com_calc_method = Column(String(50))
    car_com_paid_from = Column(String(100))
    car_com_paid_to = Column(String(100))
    car_com_date = Column(String(100))
    car_com_rate = Column(String(100))
    car_com_amt = Column(String(100))
    car_policy_prem_amt = Column(String(100))
    car_policy_eff_date = Column(String(100))
    car_policy_end_date = Column(String(100))
    car_chk_run_date = Column(String(100))
    car_pol_mnths = Column(String(50))
    car_product_id = Column(String(4000))
    car_plan_cd = Column(String(4000))
    car_plan_name = Column(String(4000))
    car_state = Column(String(50))
    car_market = Column(String(50))
    car_description = Column(String(4000))
    car_insured_name = Column(String(4000))
    car_insured_fname = Column(String(4000))
    car_insured_lname = Column(String(4000))
    car_policy_year = Column(String(4000))
    load_date = Column(String(100))
    report_date = Column(String(50))
    raw_file_name = Column(String(255))

    pk_id = Column(
        UUID(as_uuid=True),
        server_default=text("gen_random_uuid()"),
        nullable=True,
        primary_key=True,
    )

    def __repr__(self):
        values = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        return f"<CommissionHeaderHistory {values}>"