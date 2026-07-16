from app.models.BaseClasses import Base
from sqlalchemy import Column, String, DateTime, Text, ForeignKey
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
from sqlalchemy.dialects.postgresql import ARRAY
import uuid
from datetime import datetime, timezone


class MembercareSalesActivity(Base):
    __tablename__ = "membercare_sales_activity"
    __table_args__ = {"schema": "wpo"}

    id = Column(
        UNIQUEIDENTIFIER,
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    customer_id = Column(
        UNIQUEIDENTIFIER,
        ForeignKey("wpo.membercare_customer.id"),
        nullable=False,
    )
    agent_pk_id = Column(
        UNIQUEIDENTIFIER,
        ForeignKey("ops_sec.users.user_id"),
        nullable=True,
    )
    recording_id = Column(
        UNIQUEIDENTIFIER,
        ForeignKey("wpo.membercare_agent_assessment_recordings.id"),
        nullable=True,
    )
    estimated_income = Column(String(50), nullable=True)
    tax_filing_status = Column(String(50), nullable=True)
    dependent_count = Column(String(50), nullable=True)
    other_coverage_offered = Column(String(200), nullable=True)
    pregnancy_status = Column(String(50), nullable=True)
    coverage_type_requested = Column(ARRAY(String), nullable=True)
    policies_discussed = Column(Text, nullable=True)
    policy_selected = Column(String(200), nullable=True)
    effective_date = Column(String(30), nullable=True)
    monthly_premium_before_credit = Column(String(50), nullable=True)
    premium_tax_credit = Column(String(50), nullable=True)
    monthly_premium_after_credit = Column(String(100), nullable=True)
    primary_care_provider = Column(String(200), nullable=True)
    prescriptions = Column(Text, nullable=True)
    anticipated_major_medical_expense = Column(Text, nullable=True)
    dental_requirements = Column(Text, nullable=True)
    consent_recorded = Column(String(100), nullable=True)
    marketplace_or_platform = Column(String(200), nullable=True)
    member_services_number = Column(String(50), nullable=True)
    other_notes = Column(Text, nullable=True)
    sale_or_not_sale = Column(String(50), nullable=True)
    reason_for_sale = Column(Text, nullable=True)
    created_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Top-level scores
    tobacco_use = Column(String(30), nullable=True)
    edited_sale_or_not_sale = Column(String(20), nullable=True)
    edited_sale_reason = Column(Text, nullable=True)
    max_total_score = Column(String(10), nullable=True)
    awarded_total_score = Column(String(10), nullable=True)
    confidence_score = Column(String(10), nullable=True)
    overall_max_score = Column(String(10), nullable=True)
    overall_awarded_score = Column(String(10), nullable=True)

    # Section scores
    s1_intro_score = Column(String(10), nullable=True)
    s1_intro_max = Column(String(10), nullable=True)
    s2_discovery_score = Column(String(10), nullable=True)
    s2_discovery_max = Column(String(10), nullable=True)
    s3_eligibility_score = Column(String(10), nullable=True)
    s3_eligibility_max = Column(String(10), nullable=True)
    s4_enrollment_score = Column(String(10), nullable=True)
    s4_enrollment_max = Column(String(10), nullable=True)
    s5_soft_skills_score = Column(String(10), nullable=True)
    s5_soft_skills_max = Column(String(10), nullable=True)
    s6_crm_score = Column(String(10), nullable=True)
    s6_crm_max = Column(String(10), nullable=True)
    s7_compliance_status = Column(String(20), nullable=True)

    # Criterion scores
    c1_intro_score = Column(String(10), nullable=True)
    c2_intro_score = Column(String(10), nullable=True)
    c3_intro_score = Column(String(10), nullable=True)
    c4_discovery_score = Column(String(10), nullable=True)
    c5_discovery_score = Column(String(10), nullable=True)
    c6_discovery_score = Column(String(10), nullable=True)
    c7_discovery_score = Column(String(10), nullable=True)
    c8_eligibility_score = Column(String(10), nullable=True)
    c9_eligibility_score = Column(String(10), nullable=True)
    c10_eligibility_score = Column(String(10), nullable=True)
    c11_enrollment_score = Column(String(10), nullable=True)
    c12_enrollment_score = Column(String(10), nullable=True)
    c13_enrollment_score = Column(String(10), nullable=True)
    c14_enrollment_score = Column(String(10), nullable=True)
    c15_soft_skills_score = Column(String(10), nullable=True)
    c16_soft_skills_score = Column(String(10), nullable=True)
    c17_soft_skills_score = Column(String(10), nullable=True)
    c18_soft_skills_score = Column(String(10), nullable=True)
    c19_crm_score = Column(String(10), nullable=True)
    c20_disclaimers_compliance = Column(String(20), nullable=True)
    c21_disclaimers_compliance = Column(String(20), nullable=True)
    c22_payment_compliance = Column(String(20), nullable=True)
    c23_payment_compliance = Column(String(20), nullable=True)
    c24_payment_compliance = Column(String(20), nullable=True)
    c25_payment_compliance = Column(String(20), nullable=True)

    def __repr__(self):
        return f"<MembercareSalesActivity(id={self.id}, customer_id={self.customer_id}, sale_or_not_sale='{self.sale_or_not_sale}')>"
