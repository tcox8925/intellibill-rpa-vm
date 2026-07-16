from app.models.BaseClasses import Base
from sqlalchemy import Column, String, Date, Numeric
from sqlalchemy.dialects.postgresql import UUID
import uuid


class CommissionMainMV(Base):
    __tablename__ = "com_commissions_main_mv"
    __table_args__ = {"schema": "wpo"}

    txn_id = Column(String, primary_key=True)
    carrier_id = Column(String, primary_key=True)
    car_policy_id = Column(String, primary_key=True)

    carrier_name = Column(String)
    car_statement_date = Column(Date)
    car_statement_mon = Column(String)

    load_date_dt = Column(Date)
    car_commission_type = Column(String)
    car_policy_live_cnt = Column(String)

    car_com_date = Column(String)
    car_description = Column(String)
    car_state = Column(String)

    car_report_date = Column(Date)
    company_name = Column(String)
    entity_name = Column(String)
    entity_id = Column(String)
    sub_entity_id = Column(String)

    product_type = Column(String)
    member_name = Column(String)

    total_received = Column(Numeric)
    com_received = Column(Numeric)
    or_received = Column(Numeric)
    bonus_received = Column(Numeric)
    adt_received = Column(Numeric)

    oth_remit_received = Column(Numeric)

    lives_count = Column(String)

    com_paid = Column(Numeric)
    bonus_paid = Column(Numeric)
    adt_paid = Column(Numeric)
    oth_remit_paid = Column(Numeric)

    or_paid = Column(Numeric)
    agility_or = Column(Numeric)
    agent_pay = Column(Numeric)

    agent_npn = Column(String)
    agent_name = Column(String)
    assignee_name = Column(String)

    a_pay = Column(Numeric)
    recruiter = Column(String)
    recruiter_name = Column(String)

    direct_upline_name = Column(String)
    top_upline_name = Column(String)

    total_paid = Column(Numeric)
    agility_profit = Column(Numeric)

    agency_1_or = Column(Numeric)
    agency_2_or = Column(Numeric)
    agency_3_or = Column(Numeric)
    agency_4_or = Column(Numeric)
    agency_5_or = Column(Numeric)
    agency_6_or = Column(Numeric)
    agency_7_or = Column(Numeric)

    agency_1_name = Column(String)
    agency_2_name = Column(String)
    agency_3_name = Column(String)
    agency_4_name = Column(String)
    agency_5_name = Column(String)
    agency_6_name = Column(String)
    agency_7_name = Column(String)

    agent_npn_name = Column(String)

    carrier_short_name = Column(String)
    market = Column(String)

    last_update = Column(Date)

    def __repr__(self):
        return f"<CommissionMainMV txn_id={self.txn_id} carrier={self.carrier_id} policy={self.car_policy_id}>"