import uuid
from sqlalchemy import Column, String, Boolean, text
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
from app.models.BaseClasses import Base

class CommissionExceptionSummary(Base):
    __tablename__ = "com_exception_summary"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4, nullable=False)
    job_id = Column(String, nullable=True)
    company_id = Column(String, nullable=True)
    company_name = Column(String, nullable=True)
    carrier_id = Column(String, nullable=True)
    carrier_name = Column(String, nullable=True)
    car_prod_npn = Column(String, nullable=True)
    car_prod_writing_num = Column(String, nullable=True)
    car_prod_name = Column(String, nullable=True)
    ex_code = Column(String, nullable=True)
    ex_message = Column(String, nullable=True)
    ex_description = Column(String, nullable=True)
    total_contracts = Column(String, nullable=True)
    report_date = Column(String, nullable=True)
    fixed = Column(String, nullable=True)
    fixed = Column(Boolean, nullable=False, default=False, server_default=text("0"))

    def __repr__(self):
        return (
            f"<CommissionExceptionSummary(pk_id={self.pk_id}, job_id={self.job_id}, "
            f"company_id={self.company_id}, carrier_id={self.carrier_id}, "
            f"car_prod_npn={self.car_prod_npn}, total_contracts={self.total_contracts}), "
            f"fixed={self.fixed})>"
        )
