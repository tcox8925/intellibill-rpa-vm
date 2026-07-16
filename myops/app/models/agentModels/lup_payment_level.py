from sqlalchemy import Column, String
from app.models.BaseClasses import Base


class LupPaymentLevel(Base):
    __tablename__ = "lup_payment_level"
    __table_args__ = {"schema": "wpo"}

    company_id   = Column(String(1000), primary_key=True)
    company_name = Column(String(1000), nullable=False, index=True)
    payment_type = Column(String(100), nullable=False, index=True)
    level        = Column(String(4000), nullable=False, index=True)

    def __repr__(self):
        return (
            f"<LupPaymentLevel("
            f"company_id='{self.company_id}', "
            f"company_name='{self.company_name}', "
            f"payment_type='{self.payment_type}', "
            f"level='{self.level}')>"
        )