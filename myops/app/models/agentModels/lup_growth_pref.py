from app.models.BaseClasses import Base
from sqlalchemy import Column
import uuid
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER, NVARCHAR

class LupGrowthPref(Base):
    __tablename__ = "lup_growth_pref"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    growth_preference = Column(NVARCHAR(255))

    def __repr__(self):
        return (
            f"<LupGrowthPref("
            f"growth_preference='{self.growth_preference}'"
            f")>"
        )