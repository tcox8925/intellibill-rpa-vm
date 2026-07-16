from app.models.BaseClasses import Base
from sqlalchemy import Column, String
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
import uuid


class CommissionTotalsCRM(Base):
    __tablename__ = "com_totals_crm"
    __table_args__ = {"schema": "com"}

    
    id = Column("Id", String, primary_key=True)
    name = Column("Name", String)
    owner = Column("Owner", String)
    email = Column("Email", String)
    secondary_email = Column("Secondary_Email", String)
    created_by = Column("Created_By", String)
    modified_by = Column("Modified_By", String)
    created_time = Column("Created_Time", String)
    modified_time = Column("Modified_Time", String)
    last_activity_time = Column("Last_Activity_Time", String)
    currency = Column("Currency", String)
    exchange_rate = Column("Exchange_Rate", String)
    email_opt_out = Column("Email_Opt_Out", String)
    layout = Column("Layout", String)
    tag = Column("Tag", String)
    statement_type = Column("Statement_Type", String)
    statement_month = Column("Statement_Month", String)
    statement_total = Column("Statement_Total", String)
    agent = Column("Agent", String)
    status = Column("Status", String)
    status_date = Column("Status_Date", String)
    carrier_name = Column("Carrier", String)
    record_image = Column("Record_Image", String)
    last_action = Column("LAST_ACTION", String)
    last_sent_time = Column("LAST_SENT_TIME", String)
    last_action_time = Column("LAST_ACTION_TIME", String)
    user_modified_time = Column("User_Modified_Time", String)
    system_related_activity_time = Column("System_Related_Activity_Time", String)
    user_related_activity_time = Column("User_Related_Activity_Time", String)
    system_modified_time = Column("System_Modified_Time", String)
    record_approval_status = Column("Record_Approval_Status", String)
    is_record_duplicate = Column("Is_Record_Duplicate", String)
    unsubscribed_mode = Column("Unsubscribed_Mode", String)
    unsubscribed_time = Column("Unsubscribed_Time", String)
    update = Column("Update", String)
    locked_s = Column("Locked__s", String)
    statement_date = Column("Statement_Date", String)
    agent_npn = Column("Agent_NPN", String)
    agent_type = Column("Agent_Type", String)
    agent_statement_no = Column("Agent_StatementNo", String)
    agent_name = Column("Agent_Name", String)
    connected_to_module_api_name = Column("Connected_To__s.module.api_name", String)
    connected_to_s = Column("Connected_To__s", String)
    
    
   
    def __repr__(self):
        values = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        return f"<CommissionTotalsCRM {values}>"