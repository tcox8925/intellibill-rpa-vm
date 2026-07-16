from sqlalchemy import Column, DateTime, func, event, Boolean
from sqlalchemy.dialects.mssql import VARCHAR, INTEGER, UNIQUEIDENTIFIER
from app.models.BaseClasses import Base  # adjust import to your project structure
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import uuid
import re



class Pch_Provider_Info(Base):
    __tablename__ = "pch_provider_info"
    __table_args__ = {"schema": "wpo"}

    # pk_id = Column(INTEGER, primary_key=True, autoincrement=True)
    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    txn_id = Column(VARCHAR(None), nullable=False, default=uuid.uuid4)   # VARCHAR(MAX)
    company_id = Column(VARCHAR(None), nullable=True)
    company_name = Column(VARCHAR(None), nullable=True)
    group_id = Column(VARCHAR(None), nullable=True)
    group_name = Column(VARCHAR(None), nullable=True)
    group_pch_ipa = Column(VARCHAR(None), nullable=True)
    status = Column(VARCHAR(None), nullable=True)
    type = Column(VARCHAR(None), nullable=True)
    npi = Column(VARCHAR(None), nullable=True)
    last_name = Column(VARCHAR(None), nullable=True)
    first_name = Column(VARCHAR(None), nullable=True)
    primary_address_1 = Column(VARCHAR(None), nullable=True)
    primary_address_2 = Column(VARCHAR(None), nullable=True)
    city = Column(VARCHAR(None), nullable=True)
    state = Column(VARCHAR(None), nullable=True)
    zip = Column(VARCHAR(None), nullable=True)
    gender = Column(VARCHAR(None), nullable=True)
    race = Column(VARCHAR(None), nullable=True)
    email = Column(VARCHAR(None), nullable=True)
    primary_speciality = Column(VARCHAR(None), nullable=True)
    secondary_speciality = Column(VARCHAR(None), nullable=True)
    professional_degree = Column(VARCHAR(None), nullable=True)
    rx_waiver_expiration_date = Column(VARCHAR(None), nullable=True)
    awards = Column(VARCHAR(None), nullable=True)
    language = Column(VARCHAR(None), nullable=True)
    board_cert = Column(VARCHAR(None), nullable=True)
    board_cert_detail = Column(VARCHAR(None), nullable=True)
    updated_on = Column(DateTime, nullable=True, default=lambda: datetime.now(timezone.utc))
    source = Column(VARCHAR(None), nullable=True)
    job_owner_name = Column(VARCHAR(None), nullable=True)
    job_owner_email = Column(VARCHAR(None), nullable=True)
    job_owner_id = Column(VARCHAR(None), nullable=True)
    caqh_number = Column(VARCHAR(None), nullable=True)
    touch_user = Column(VARCHAR(100), nullable=False, default='Untouched')
    last_touch = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    caqh_status = Column(Boolean, nullable=True)

    locations = relationship(
        "Pch_Provider_Location",
        primaryjoin="Pch_Provider_Info.txn_id == foreign(Pch_Provider_Location.txn_id_provider)",
        viewonly=True,
    )

    identifiers = relationship(
        "Pch_Provider_Identifiers",
        primaryjoin="Pch_Provider_Info.txn_id == foreign(Pch_Provider_Identifiers.txn_id_provider)",
        viewonly=True,
    )

    education = relationship(
        "Pch_Provider_Education",
        primaryjoin="Pch_Provider_Info.txn_id == foreign(Pch_Provider_Education.txn_id_provider)",
        viewonly=True,
    )

    regulatory_validation = relationship(
        "Pch_Regulatory_Validation",
        primaryjoin="Pch_Provider_Info.txn_id == foreign(Pch_Regulatory_Validation.txn_id_provider)",
        viewonly=True,
    )

    affiliations = relationship(
        "Pch_Affiliations",
        primaryjoin="Pch_Provider_Info.txn_id == foreign(Pch_Affiliations.txn_id_provider)",
        viewonly=True,
    )

    carriers = relationship(
        "pch_carriers",
        primaryjoin="Pch_Provider_Info.txn_id == foreign(pch_carriers.txn_id_provider)",
        viewonly=True,
    )

    def __repr__(self):
        return (
            f"<Pch_Provider_Info("
            f"status='{self.status}', "
            f"txn_id='{self.txn_id}', "
            f"company_id='{self.company_id}', "
            f"group_id='{self.group_id}', "
            f"group_name='{self.group_name}',"
            f"state='{self.state}', "
            f"caqh_status='{self.caqh_status}', "
            f"job_owner_name='{self.job_owner_name}', "
            f"npi='{self.npi}')>"
        )

def is_valid_email(email: str) -> bool:
    """Basic regex to validate an email address."""
    if not email:
        return False
    pattern = r"^[\w\.-]+@[\w\.-]+\.\w+$"
    return re.match(pattern, email) is not None

@event.listens_for(Pch_Provider_Info, "before_update", propagate=True)
def receive_before_update(mapper, connection, target):
    target.last_touch = datetime.now(timezone.utc)
    target.updated_on = datetime.now(timezone.utc)

@event.listens_for(Pch_Provider_Info, "before_insert")
def validate_touch_user_insert(mapper, connection, target):
    if target.touch_user != 'Untouched' and not is_valid_email(target.touch_user):
        raise ValueError(f"touch_user must be a valid email address, got '{target.touch_user}'")

@event.listens_for(Pch_Provider_Info, "before_update")
def validate_touch_user_update(mapper, connection, target):
    if target.touch_user and not is_valid_email(target.touch_user):
        raise ValueError(f"touch_user must be a valid email address, got '{target.touch_user}'")
