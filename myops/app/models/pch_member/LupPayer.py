import uuid
from sqlalchemy import Column, String, Text, Boolean
from sqlalchemy.dialects.postgresql import UUID
from app.models.BaseClasses import Base


class LupPayers(Base):
    __tablename__ = "lup_payers"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4,nullable=False)
    payer_name = Column(Text, nullable=True)
    payer_id = Column(Text, nullable=True)
    transaction_type = Column(Text, nullable=True)
    available = Column(Text, nullable=True)
    non_par = Column(Text, nullable=True)
    enrollment = Column(Text, nullable=True)
    secondary = Column(Text, nullable=True)
    attachment = Column(Text, nullable=True)
    wc_auto = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    payer_type = Column(String(50), nullable=True)
    active_status = Column(Boolean, nullable=True)

    def __repr__(self):
        values = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        return f"<LupPayers {values}>"