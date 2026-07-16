from app.models.BaseClasses import Base
from sqlalchemy import Column
import uuid
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER, NVARCHAR

class AddressType(Base):
    __tablename__ = "lup_addrtype"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    type = Column(NVARCHAR(25))

    def __repr__(self):
        return (
            f"<LupAddressType("
            f"type='{self.type}'"
            f")>"
        )
