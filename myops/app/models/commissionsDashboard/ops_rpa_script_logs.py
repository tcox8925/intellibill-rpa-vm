import uuid
from sqlalchemy import Column
from sqlalchemy.dialects.mssql import NVARCHAR, UNIQUEIDENTIFIER
from app.models.BaseClasses import Base


class OpsRpaScriptLogs(Base):
    __tablename__ = "ops_rpa_script_logs"
    __table_args__ = {"schema": "wpo"}

    script_name = Column(NVARCHAR(None), nullable=True)
    start_datetime = Column(NVARCHAR(None), nullable=True)
    end_datetime = Column(NVARCHAR(None), nullable=True)
    error = Column(NVARCHAR(None), nullable=True)
    success = Column(NVARCHAR(None), nullable=True)
    file_status = Column(NVARCHAR(None), nullable=True)
    file_path = Column(NVARCHAR(None), nullable=True)
    process_type = Column(NVARCHAR(None), nullable=True)
    file_report_month = Column(NVARCHAR(None), nullable=True)
    file_com_month = Column(NVARCHAR(None), nullable=True)
    company_id = Column(NVARCHAR(None), nullable=True)
    carrier_id = Column(NVARCHAR(None), nullable=True)
    product_name = Column(NVARCHAR(None), nullable=True)
    flow_id = Column(NVARCHAR(None), nullable=True)
    run_id = Column(NVARCHAR(None), nullable=True)

    pk_id = Column(
        UNIQUEIDENTIFIER,
        primary_key=True,
        default=uuid.uuid4,
        nullable=False
    )

    def __repr__(self):
        return (
            f"<OpsRpaScriptLogs("
            f"script_name='{self.script_name}', "
            f"start_datetime='{self.start_datetime}', "
            f"end_datetime='{self.end_datetime}', "
            f"process_type='{self.process_type}', "
            f"file_status='{self.file_status}', "
            f"company_id='{self.company_id}', "
            f"carrier_id='{self.carrier_id}', "
            f"run_id='{self.run_id}'"
            f")>"
        )
