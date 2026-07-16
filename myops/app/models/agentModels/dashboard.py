from sqlalchemy import Column, String, Integer, Date, Boolean
from app.models.BaseClasses import Base

class BobCarrierSummaryMV(Base):
    __tablename__ = "bob_carrier_summary_mv"
    __table_args__ = {"schema": "wpo"}

    agent_npn = Column(String, primary_key=True)
    agent_name = Column(String)
    report_date = Column(Date, primary_key=True)
    carrier_short_name = Column(String, primary_key=True)
    total_policies = Column(Integer)
    total_members = Column(Integer)

class BobCarrierMembershipsMV(Base):
    __tablename__ = "bob_carrier_memberships_mv"
    __table_args__ = {"schema": "wpo"}

    carrier_id = Column(Integer, primary_key=True)
    report_date = Column(Date, primary_key=True)
    agent_npn = Column(String, primary_key=True)
    txn_status = Column(String)
    agent_full_name = Column(String)
    contract_count = Column(Integer)
    mem_count = Column(Integer)
    mem_effective_date = Column(Date)
    mem_market = Column(String)
    entity_id = Column(String)
    sub_entity_id = Column(String)
    carrier_short_name = Column(String)

class VwFileLogs(Base):
    __tablename__ = "vw_file_logs"
    __table_args__ = {"schema": "wpo"}

    carrier_short_name = Column(String, primary_key=True)
    process_type = Column(String, primary_key=True)
    report_date = Column(String, primary_key=True)
    process_date_end = Column(Date)
    txn_tot_cnt = Column(Integer)
    txn_process_cnt = Column(Integer)
    txn_error_cnt = Column(Integer)
    load_status = Column(String)
    file_name = Column(String)

class TimeSeriesForecasts(Base):
    __tablename__ = "time_series_forecasts"
    __table_args__ = {"schema": "wpo"}

    forecast_month = Column(Date, primary_key=True)
    metric_name = Column(String, primary_key=True)
    process_name = Column(String, primary_key=True)
    prediction = Column(Integer)
    lower_ci = Column(Integer)
    upper_ci = Column(Integer)
    is_active = Column(Boolean)
