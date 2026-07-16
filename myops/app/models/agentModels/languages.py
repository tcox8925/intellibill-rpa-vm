from app.models.BaseClasses import Base
from sqlalchemy import Column
import uuid
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER, NVARCHAR

class Languages(Base):
    __tablename__ = "lup_languages"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    language = Column(NVARCHAR(150))

    def __repr__(self):
        return (
            f"<LupLanguages("
            f"language='{self.language}'"
            f")>"
        )
