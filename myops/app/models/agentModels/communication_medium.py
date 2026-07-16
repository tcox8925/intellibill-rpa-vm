from app.models.BaseClasses import Base
from sqlalchemy import Column
import uuid
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER, NVARCHAR

class CommunicationMedium(Base):
    __tablename__ = "lup_communication_medium"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    type = Column(NVARCHAR(50))
    sub_type = Column(NVARCHAR(50))

    def __repr__(self):
        return (
            f"<LupCommunicationMedium("
            f"type='{self.type}', sub_type='{self.sub_type}'"
            f")>"
        )
