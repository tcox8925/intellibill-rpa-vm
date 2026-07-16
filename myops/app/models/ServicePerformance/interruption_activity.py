from sqlalchemy import Column, Date, Text, ForeignKey, text, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID
from app.models.BaseClasses import Base

class InterruptionActivity(Base):
    __tablename__ = "interruption_activity"
    __table_args__ = {"schema": "ops_srv"}

    pk_id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    interruption_id = Column(UUID(as_uuid=True), ForeignKey("ops_srv.service_interruption.id", ondelete="CASCADE"), nullable=False)
    description = Column(Text, nullable=True)
    date = Column(TIMESTAMP, nullable=False)
    type = Column(Text, nullable=False)

    def __repr__(self):
        return (
            f"<InterruptionActivity(pk_id={self.pk_id}, "
            f"interruption_id={self.interruption_id}, "
            f"type='{self.type}', "
            f"date={self.date}, "
            f"description='{(self.description)}'>"
        )
