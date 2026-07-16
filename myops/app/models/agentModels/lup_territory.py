from sqlalchemy import Column, String
from app.models.BaseClasses import Base


class LupTerritory(Base):
    __tablename__ = "lup_territory"
    __table_args__ = {"schema": "wpo"}

    company_id   = Column(String(1000), primary_key=True)
    company_name = Column(String(1000), nullable=False, index=True)
    territory    = Column(String(4000), nullable=False, index=True)

    def __repr__(self):
        return (
            f"<LupTerritory("
            f"company_id='{self.company_id}', "
            f"company_name='{self.company_name}', "
            f"territory='{self.territory}')>"
        )