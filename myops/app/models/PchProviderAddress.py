import uuid
from sqlalchemy import Column, ForeignKey, Boolean
from sqlalchemy.dialects.postgresql import UUID, VARCHAR
from app.models.BaseClasses import Base


class Pch_Provider_Address(Base):
    __tablename__ = "pch_provider_address"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    provider_id = Column(UUID(as_uuid=True), nullable=False)
    provider_npi = Column(VARCHAR(20), nullable=False)

    address_type_id = Column(
        UUID(as_uuid=True),
        ForeignKey("wpo.lup_addrtype.pk_id"),
        nullable=False
    )

    line1 = Column(VARCHAR(500), nullable=True)
    line2 = Column(VARCHAR(500), nullable=True)
    street = Column(VARCHAR(255), nullable=True)
    city = Column(VARCHAR(100), nullable=True)
    state = Column(VARCHAR(100), nullable=True)
    zip = Column(VARCHAR(20), nullable=True)
    county = Column(VARCHAR(150), nullable=True)

    primary = Column(Boolean, nullable=True, default=False)

    def __repr__(self):
        return (
            f"<Pch_Provider_Address("
            f"provider_id='{self.provider_id}', "
            f"provider_npi='{self.provider_npi}', "
            f"address_type_id='{self.address_type_id}', "
            f"line1='{self.line1}', "
            f"line2='{self.line2}', "
            f"street='{self.street}', "
            f"city='{self.city}', "
            f"state='{self.state}', "
            f"zip='{self.zip}', "
            f"county='{self.county}', "
            f"primary='{self.primary}'"
            f")>"
        )
