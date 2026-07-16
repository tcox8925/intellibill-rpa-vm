from app.models.BaseClasses import Base
from sqlalchemy import Column, String
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
import uuid


class CommissionProcessHistory(Base):
    __tablename__ = "com_process_history"
    __table_args__ = {"schema": "wpo"}


    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4, nullable=False)
    
    job_id = Column(String)
    company_id = Column(String)
    process_type = Column(String)
    carrier_id = Column(String)
    product_id = Column(String)
    report_month = Column(String)
    com_month = Column(String)
    file_name = Column(String)
    job_status = Column(String)
    commission_status = Column(String)
    job_start_datetime = Column(String)
    job_update_datetime = Column(String)
    job_end_datetime = Column(String)
    product_name = Column(String)
    job_owner_name = Column(String)
    job_owner_email = Column(String)
    

    def __repr__(self):
        values = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        return f"<CommissionProcessHistory {values}>"