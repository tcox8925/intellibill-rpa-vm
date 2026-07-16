from sqlalchemy import Column, Integer, String, Text
from app.models.BaseClasses import Base


class OpsAutomationSummary(Base):
    __tablename__ = "ops_automation_summary_vw"

    vendor_name = Column(String(100), primary_key=True, index=True, comment="Vendor name")
    carrier_id = Column(Text, nullable=True, comment="Carrier ID")
    market = Column(String(100), nullable=True, comment="Market")
    carrier_short_name = Column(Text, nullable=True, comment="Carrier short name")
    carrier_status = Column(Text, nullable=True, comment="Carrier status")
    acc_dev_flag = Column(Text, nullable=True, comment="ACC dev flag")
    acc_active_flag = Column(Text, nullable=True, comment="ACC active flag")
    acr_active_flag = Column(String, nullable=True, comment="ACR active flag")
    acr_dev_flag = Column(Text, nullable=True, comment="ACR dev flag")
    acr_automation_type = Column(Text, nullable=True, comment="ACR automation type")
    bob_download = Column(String, nullable=True, comment="BOB download")
    acu_download = Column(String, nullable=True, comment="ACU download")
    com_download = Column(String, nullable=True, comment="COM download")
    bob_process = Column(String, nullable=True, comment="BOB process")
    acu_process = Column(String, nullable=True, comment="ACU process")
    com_process = Column(String, nullable=True, comment="COM process")

    def __repr__(self):
        return f"<OpsAutomationSummary(vendor_name='{self.vendor_name}', carrier_short_name='{self.carrier_short_name}')>"
