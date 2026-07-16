import uuid
from sqlalchemy import Column, String
from sqlalchemy.dialects.postgresql import UUID
from app.models.BaseClasses import Base


class PchFinSummary(Base):
    __tablename__ = "pch_fin_summary"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    month = Column("Month", String, key="month", nullable=True)
    member_months = Column("Member Months", String, key="member_months", nullable=True)
    inpatient = Column("Inpatient", String, key="inpatient", nullable=True)
    outpatient = Column("Outpatient", String, key="outpatient", nullable=True)
    primary_care = Column("Primary Care", String, key="primary_care", nullable=True)
    specialty = Column("Specialty", String, key="specialty", nullable=True)
    net_rx = Column("Net Rx", String, key="net_rx", nullable=True)
    net_other_medical = Column(
        "Net Other Medical", String, key="net_other_medical", nullable=True
    )
    report_date = Column(String, nullable=True)
    company_id = Column(String, nullable=True)
    carrier_id = Column(String, nullable=True)
    load_date = Column(String, nullable=True)
    source = Column(String, nullable=True)
    source_type = Column(String, nullable=True)

    def __repr__(self):
        values = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        return f"<PchFinSummary {values}>"
