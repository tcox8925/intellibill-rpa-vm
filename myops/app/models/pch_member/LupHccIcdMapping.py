import uuid
from sqlalchemy import Column, String
from sqlalchemy.dialects.postgresql import UUID
from app.models.BaseClasses import Base


class LupHccIcdMapping(Base):
    __tablename__ = "lup_hcc_icd_mapping"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    diagnosis_code = Column(String, nullable=True)
    description = Column(String, nullable=True)
    cms_hcc_v22 = Column(String, nullable=True)
    cms_hcc_v24 = Column(String, nullable=True)
    cms_hcc_v28 = Column(String, nullable=True)
    cms_hcc_v22_2026 = Column(String, nullable=True)
    cms_hcc_v24_2026 = Column(String, nullable=True)
    cms_hcc_v28_2026 = Column(String, nullable=True)
    cms_hcc_v28_desc = Column(String, nullable=True)

    def __repr__(self):
        values = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        return f"<LupHccIcdMapping {values}>"
