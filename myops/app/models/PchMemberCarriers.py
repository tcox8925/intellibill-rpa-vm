from uuid import uuid4

from sqlalchemy import Column, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.types import DateTime  # If you later add timestamps
from app.models.BaseClasses import Base


class Pch_Member_Carriers(Base):
    __tablename__ = "pch_member_carriers"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UUID(as_uuid=True), primary_key=True, nullable=False, default=uuid4)

    payer_id = Column(String(20), nullable=False)
    payer_name = Column(String(255), nullable=False)

    payer_type = Column(String(50), nullable=True)
    claim_payer_id = Column(String(20), nullable=True)
    eligibility_payer_id = Column(String(20), nullable=True)
    era_payer_id = Column(String(20), nullable=True)

    address_line1 = Column(String(255), nullable=True)
    address_line2 = Column(String(255), nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(10), nullable=True)
    zip_code = Column(String(20), nullable=True)
    country = Column(String(100), nullable=True)

    def __repr__(self):
        return (
            f"<PchMemberCarriers("
            f"pk_id='{self.pk_id}', "
            f"payer_id='{self.payer_id}', "
            f"payer_name='{self.payer_name}', "
            f"payer_type='{self.payer_type}', "
            f"claim_payer_id='{self.claim_payer_id}', "
            f"eligibility_payer_id='{self.eligibility_payer_id}', "
            f"era_payer_id='{self.era_payer_id}', "
            f"address_line1='{self.address_line1}', "
            f"address_line2='{self.address_line2}', "
            f"city='{self.city}', "
            f"state='{self.state}', "
            f"zip_code='{self.zip_code}', "
            f"country='{self.country}'"
            f")>"
        )
