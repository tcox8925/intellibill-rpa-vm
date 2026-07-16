import uuid
from sqlalchemy import Column, String, event
from sqlalchemy.dialects.postgresql import UUID
from app.models.BaseClasses import Base
from datetime import datetime, timezone

class Pch_Carrier_Credentials(Base):
    __tablename__ = "pch_carrier_credentials"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    txn_id_provider = Column(UUID(as_uuid=True), nullable=False)
    status = Column(String(50), nullable=True)
    credential_type = Column(String(100), nullable=True)
    carrier_id = Column(String(50), nullable=True)
    carrier_name = Column(String(150), nullable=True)
    carrier_market = Column(String(100), nullable=True)
    carrier_plan = Column(String(150), nullable=True)
    date_time = Column(String(50), nullable=True)
    login = Column(String(50), nullable=True)
    module = Column(String(150), nullable=True)

    def __repr__(self):
        return (
            f"<Pch_Carrier_Credentials(pk_id={self.pk_id}, txn_id_provider={self.txn_id_provider}, "
            f"status={self.status}, credential_type={self.credential_type}, "
            f"carrier_id={self.carrier_id}, carrier_name={self.carrier_name}, "
            f"carrier_market={self.carrier_market}, carrier_plan={self.carrier_plan})>"
        )

@event.listens_for(Pch_Carrier_Credentials, "before_update", propagate=True)
def receive_before_update(mapper, connection, target):
    target.date_time = str(datetime.now(timezone.utc))

@event.listens_for(Pch_Carrier_Credentials, "before_insert")
def receive_before_update(mapper, connection, target):
    target.date_time = str(datetime.now(timezone.utc))