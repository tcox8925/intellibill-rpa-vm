from app.models.BaseClasses import Base
from sqlalchemy import Column, ForeignKey, DateTime, Boolean, func
from sqlalchemy.dialects.postgresql import UUID, VARCHAR
import uuid


class Pch_Provider_Communication(Base):
    __tablename__ = "pch_provider_communication"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_id = Column(UUID(as_uuid=True), nullable=False)
    provider_npi = Column(VARCHAR(20), nullable=False)

    communication_id = Column(
        UUID(as_uuid=True),
        ForeignKey("wpo.lup_communication_medium.pk_id"),
        nullable=False
    )

    value = Column(VARCHAR(100), nullable=True)
    text_opt = Column(Boolean, default=False, nullable=False)
    dnd = Column(VARCHAR(100), nullable=True)
    ai_pre_recording = Column(Boolean, nullable=True)
    marketing_opt_in = Column(Boolean, nullable=True)

    created_at = Column(
        DateTime,
        server_default=func.current_timestamp(),
        nullable=False
    )
    updated_at = Column(DateTime, nullable=True)
    primary = Column(Boolean, nullable=True, default=False)
    extension = Column(VARCHAR(100), nullable=True)

    def __repr__(self):
        return (
            f"<Pch_Provider_Communication("
            f"provider_id='{self.provider_id}', "
            f"provider_npi='{self.provider_npi}', "
            f"communication_id='{self.communication_id}', "
            f"value='{self.value}', "
            f"text_opt='{self.text_opt}', "
            f"dnd='{self.dnd}', "
            f"ai_pre_recording='{self.ai_pre_recording}', "
            f"marketing_opt_in='{self.marketing_opt_in}', "
            f"created_at='{self.created_at}', "
            f"updated_at='{self.updated_at}', "
            f"primary='{self.primary}', "
            f"extension='{self.extension}'"
            f")>"
        )
