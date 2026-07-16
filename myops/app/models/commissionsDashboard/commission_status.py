from app.models.BaseClasses import Base
from sqlalchemy import Column, String
import uuid
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER

class CommissionStatus(Base):
    __tablename__ = "lup_com_status"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)

    commission_status = Column(String)

    def __repr__(self):
        return (
            f"<CommissionStatus("
            f"commission_status='{self.commission_status}'"
            f")>"
        )
