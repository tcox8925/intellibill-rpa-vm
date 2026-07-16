from app.models.BaseClasses import Base
from sqlalchemy import Column
import uuid
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER, NVARCHAR

class Social(Base):
    __tablename__ = "lup_social"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    platform = Column(NVARCHAR(25))

    def __repr__(self):
        return (
            f"<LupSocial("
            f"platform='{self.platform}'"
            f")>"
        )
