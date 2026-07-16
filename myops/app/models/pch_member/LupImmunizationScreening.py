from sqlalchemy import Column, String, Text
from sqlalchemy.dialects.postgresql import UUID
from app.models.BaseClasses import Base
import uuid

class LupImmunizationScreening(Base):
    __tablename__ = "lup_immunization_screening"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False
    )

    type = Column(String(30), nullable=False)
    category = Column(String(200), nullable=True)
    procedure = Column(String(200), nullable=True)
    gender = Column(String(10), nullable=True)
    age_range = Column(String(30), nullable=True)
    requirement = Column(Text, nullable=True)
    condition = Column(Text, nullable=True)
    source = Column(String(100), nullable=True)

    def __repr__(self):
        values = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        return (
            f"<LupImmunizationScreening({values})>"
        )
