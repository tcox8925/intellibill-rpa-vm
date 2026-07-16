from app.models.BaseClasses import Base
from sqlalchemy import Column
import uuid
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER, NVARCHAR

class Salutation(Base):
    __tablename__ = "lup_salutation"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    salutation = Column(NVARCHAR(25))

    def __repr__(self):
        return (
            f"<LupSalutation("
            f"salutation='{self.salutation}'"
            f")>"
        )
