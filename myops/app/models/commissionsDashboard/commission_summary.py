import uuid
from sqlalchemy import Column, String, UniqueConstraint
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
from app.models.BaseClasses import Base

class CommissionSummary(Base):
    __tablename__ = "com_carrier_statement_totals"
    __table_args__ = {"schema": "wpo"}  # schema name

    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    job_id = Column(String, nullable=True)
    company_id = Column(String, nullable=True)
    company_name = Column(String, nullable=True)
    carrier_id = Column(String, nullable=True)
    carrier_name = Column(String, nullable=True)
    product_name = Column(String, nullable=True)
    commission_month = Column(String, nullable=True)
    report_month = Column(String, nullable=True)
    total_agents = Column(String, nullable=True)
    total_BOB_agents = Column(String, nullable=True)
    total_policies = Column(String, nullable=True)
    total_BOB_policies = Column(String, nullable=True)
    total_agility_received = Column(String, nullable=True)
    total_revenue_received = Column(String, nullable=True)
    agility_profit = Column(String, nullable=True)
    total_commissions_received = Column(String, nullable=True)
    total_commissions_paid = Column(String, nullable=True)
    total_overrides_received = Column(String, nullable=True)
    total_overrides_paid = Column(String, nullable=True)
    total_bonus_received = Column(String, nullable=True)
    total_bonus_paid = Column(String, nullable=True)
    total_adjustments_received = Column(String, nullable=True)
    total_adjustments_paid = Column(String, nullable=True)
    average_commission_paid = Column(String, nullable=True)
    average_override_paid = Column(String, nullable=True)
    average_bonus_paid = Column(String, nullable=True)
    book_vs_commission_variance = Column(String, nullable=True)

    def __repr__(self):
        return (
            f"<ComCarrierStatementTotals(job_id={self.job_id}, "
            f"company_id={self.company_id}, carrier_id={self.carrier_id}, "
            f"product_name={self.product_name}, report_month={self.report_month})>"
        )
