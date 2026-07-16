from sqlalchemy import Column, NVARCHAR
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
from app.models.BaseClasses import Base


class LevelCategory(Base):
    __tablename__ = "lup_level_category"
    __table_args__ = {"schema": "wpo"}

    company_id = Column(NVARCHAR(None), nullable=True)
    company_name = Column(NVARCHAR(None), nullable=True)
    level_cat = Column(NVARCHAR(None), nullable=True)
    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, nullable=False)

    def __repr__(self):
        return (
            f"<LevelCategory(pk_id={self.pk_id}, "
            f"company_id={self.company_id}, "
            f"company_name={self.company_name}, "
            f"level_cat={self.level_cat})>"
        )
