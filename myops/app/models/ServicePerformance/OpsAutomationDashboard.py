from app.models.BaseClasses import Base
from sqlalchemy import Column, BigInteger, Integer, Text, Date, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict

class OpsAutomationDashboard(Base):
    __tablename__ = "ops_automation_dashboard"
    __schema__ = "ops_srv"
    __table_args__ = {"schema": __schema__}

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    record_date = Column(Date, nullable=False)
    carrier_id = Column(BigInteger, nullable=False)
    carrier_name = Column(Text, nullable=True)
    carrier_status = Column(Text, nullable=True)
    
    acu_cadence = Column(Text, nullable=True)
    bob_cadence = Column(Text, nullable=True)
    com_cadence = Column(Text, nullable=True)
    acc_cadence = Column(Text, nullable=True)
    acr_cadence = Column(Text, nullable=True)

    acu_download = Column(Integer, nullable=True, default=0)
    acu_process = Column(Integer, nullable=True, default=0)
    acu_status = Column(Integer, nullable=True, default=0)

    bob_download = Column(Integer, nullable=True, default=0)
    bob_process = Column(Integer, nullable=True, default=0)
    bob_status = Column(Integer, nullable=True, default=0)

    com_download = Column(Integer, nullable=True, default=0)
    com_process = Column(Integer, nullable=True, default=0)
    com_status = Column(Integer, nullable=True, default=0)

    acc_rpa = Column(Integer, nullable=True, default=0)
    acc_automated = Column(Integer, nullable=True, default=0)
    acc_process = Column(Integer, nullable=True, default=0)
    acc_status = Column(Integer, nullable=True, default=0)

    acr_rpa = Column(Integer, nullable=True, default=0)
    acr_automated = Column(Integer, nullable=True, default=0)
    acr_process = Column(Integer, nullable=True, default=0)
    acr_status = Column(Integer, nullable=True, default=0)

    acu_automated = Column(Integer, nullable=True, default=0)
    acu_proc_automated = Column(Integer, nullable=True, default=0)

    bob_automated = Column(Integer, nullable=True, default=0)
    bob_proc_automated = Column(Integer, nullable=True, default=0)

    com_automated = Column(Integer, nullable=True, default=0)
    com_proc_automated = Column(Integer, nullable=True, default=0)

    last_updated = Column(DateTime(timezone=True), nullable=True)

    # acc_priority = Column(Text, nullable=True)
    # acr_priority = Column(Text, nullable=True)
    # acu_priority = Column(Text, nullable=True)
    # bob_priority = Column(Text, nullable=True)
    # com_priority = Column(Text, nullable=True)
    acu_automation_type = Column(Text, nullable=True)
    bob_automation_type = Column(Text, nullable=True)
    com_automation_type = Column(Text, nullable=True)
    acc_automation_type = Column(Text, nullable=True)
    acr_automation_type = Column(Text, nullable=True)
    interruptions = Column(MutableDict.as_mutable(JSONB))
    notes = Column(MutableDict.as_mutable(JSONB))
    cadence_desc = Column(
        JSONB,
        nullable=False,
        default=dict  # equivalent to '{}'::jsonb
    )
    last_run_date = Column(
        JSONB,
        nullable=True
    )
    carry_over_flag = Column(JSONB, nullable=True, default=dict)
    entity_id = Column(Text, nullable=True)
    sub_entity_id = Column(Text, nullable=True)
    
    def status_mapping(status_value):
        status_map = {
            'InActive': 0,
            'Active': 1,
            'No Action': 2,
            'In Development': 3
        }
        return status_map.get(status_value)  # Default to 1 for any unmapped value

    def __repr__(self):
        return (
            f"<OpsAutomationDashboard(id={self.id}, "
            f"record_date={self.record_date}, carrier_id={self.carrier_id}, "
            f"carrier_name='{self.carrier_name}', carrier_status='{self.carrier_status}', "
            f"last_updated={self.last_updated}, interruptions={self.interruptions}, notes={self.notes})>"
        )