from app.models.BaseClasses import Base
from sqlalchemy import Column, String
import uuid
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER

class CommissionHeader(Base):
    __tablename__ = "com_header"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)

    job_id = Column(String, nullable=True)
    txn_status = Column(String, nullable=True)
    txn_id = Column(String, nullable=True)
    company_id = Column(String, nullable=True)
    company_name = Column(String, nullable=True)
    carrier_id = Column(String, nullable=True)
    carrier_name = Column(String, nullable=True)
    car_prod_name = Column(String, nullable=True)
    car_prod_fname = Column(String, nullable=True)
    car_prod_lname = Column(String, nullable=True)
    car_prod_npn = Column(String, nullable=True)
    car_prod_writing_num = Column(String, nullable=True)
    car_statement_date = Column(String, nullable=True)
    car_commission_type = Column(String, nullable=True)
    car_policy_id = Column(String, nullable=True)
    car_policy_live_cnt = Column(String, nullable=True)
    car_com_split_rate = Column(String, nullable=True)
    car_com_calc_method = Column(String, nullable=True)
    car_com_paid_from = Column(String, nullable=True)
    car_com_paid_to = Column(String, nullable=True)
    car_com_date = Column(String, nullable=True)
    car_com_rate = Column(String, nullable=True)
    car_com_amt = Column(String, nullable=True)
    car_policy_prem_amt = Column(String, nullable=True)
    car_policy_eff_date = Column(String, nullable=True)
    car_policy_end_date = Column(String, nullable=True)
    car_chk_run_date = Column(String, nullable=True)
    car_pol_mnths = Column(String, nullable=True)
    car_product_id = Column(String, nullable=True)
    car_plan_cd = Column(String, nullable=True)
    car_plan_name = Column(String, nullable=True)
    car_state = Column(String, nullable=True)
    car_market = Column(String, nullable=True)
    car_description = Column(String, nullable=True)
    car_insured_name = Column(String, nullable=True)
    car_insured_fname = Column(String, nullable=True)
    car_insured_lname = Column(String, nullable=True)
    car_policy_year = Column(String, nullable=True)
    load_date = Column(String, nullable=True)
    report_date = Column(String, nullable=True)
    raw_file_name = Column(String, nullable=True)

    def __repr__(self):
        return (
            f"<CommissionHeader("
            f"job_id='{self.job_id}', "
            f"txn_status='{self.txn_status}', "
            f"txn_id='{self.txn_id}', "
            f"company_id='{self.company_id}', "
            f"company_name='{self.company_name}', "
            f"carrier_id='{self.carrier_id}', "
            f"carrier_name='{self.carrier_name}', "
            f"car_prod_name='{self.car_prod_name}', "
            f"car_prod_fname='{self.car_prod_fname}', "
            f"car_prod_lname='{self.car_prod_lname}', "
            f")>"
        )
