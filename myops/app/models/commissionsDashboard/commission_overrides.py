import uuid
from sqlalchemy import Column
from sqlalchemy.dialects.mssql import NVARCHAR, UNIQUEIDENTIFIER
from app.models.BaseClasses import Base


class CommissionOverrides(Base):
    __tablename__ = "com_overrides"
    __table_args__ = {"schema": "wpo"}

    job_id = Column(NVARCHAR(None), nullable=True)
    txn_id_com_header = Column(NVARCHAR(None), nullable=True)
    company_id = Column(NVARCHAR(None), nullable=True)
    company_name = Column(NVARCHAR(None), nullable=True)
    carrier_id = Column(NVARCHAR(None), nullable=True)
    carrier_name = Column(NVARCHAR(None), nullable=True)
    ra_level = Column(NVARCHAR(None), nullable=True)
    ra_npn = Column(NVARCHAR(None), nullable=True)
    ra_name = Column(NVARCHAR(None), nullable=True)
    ra_id = Column(NVARCHAR(None), nullable=True)
    ra_rate = Column(NVARCHAR(None), nullable=True)
    ra_pay = Column(NVARCHAR(None), nullable=True)
    ra_comm_type = Column(NVARCHAR(None), nullable=True)
    ra_statement_no = Column(NVARCHAR(None), nullable=True)
    report_date = Column(NVARCHAR(None), nullable=True)
    load_date = Column(NVARCHAR(None), nullable=True)
    raw_file_name = Column(NVARCHAR(None), nullable=True)

    pk_id = Column(
        UNIQUEIDENTIFIER,
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )

    def __repr__(self):
        return (
            f"<ComOverrides("
            f"pk_id={self.pk_id}, "
            f"job_id={self.job_id}, "
            f"txn_id_com_header={self.txn_id_com_header}, "
            f"company_id={self.company_id}, "
            f"company_name={self.company_name}, "
            f"carrier_id={self.carrier_id}, "
            f"carrier_name={self.carrier_name}, "
            f"ra_level={self.ra_level}, "
            f"ra_npn={self.ra_npn}, "
            f"ra_name={self.ra_name}, "
            f"ra_rate={self.ra_rate}, "
            f"ra_pay={self.ra_pay}, "
            f"ra_comm_type={self.ra_comm_type}"
            f")>"
        )
