from sqlalchemy import Column, String
from app.models.BaseClasses import Base  # Your standard import


class LupRateType(Base):
    __tablename__ = "lup_rate_type"
    __table_args__ = {"schema": "wpo"}

    company_id   = Column(String(1000), primary_key=True)
    company_name = Column(String(1000), nullable=False, index=True)
    rate_type    = Column(String(4000), nullable=False, index=True)

    def __repr__(self):
        return (
            f"<LupRateType(pk_id={self.pk_id}, "
            f"company_id='{self.company_id}', "
            f"company_name='{self.company_name}', "
            f"rate_type='{self.rate_type}')"
        )