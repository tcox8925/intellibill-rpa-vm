from app.models.BaseClasses import Base
from sqlalchemy import JSON, Column, DateTime, String, Text, text
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
from sqlalchemy.dialects.postgresql import UUID
import uuid


class CrmTicket(Base):
    __tablename__ = "crm_tickets"
    __table_args__ = {"schema": "wpo"} 

    pk_id = Column(UNIQUEIDENTIFIER,primary_key=True,default=uuid.uuid4, nullable=False)
    ticket_id = Column(String(100), nullable=False, unique=True)
    subject = Column(String(255), nullable=True)
    description = Column(String, nullable=True)  
    type = Column(String(100), nullable=True)
    status = Column(String(50), nullable=True)
    created_at = Column(
        DateTime,
        server_default=text("SYSDATETIME()"),
        nullable=False,
    )
    created_by = Column(String(150), nullable=True)
    owner = Column(String(200), nullable=True)
    attachment = Column(JSON, nullable=True)
    entity_id = Column(String, nullable=True)
    sub_entity_id = Column(String, nullable=True)
    resolution = Column(Text, nullable=True)
    agent_email = Column(String(320), nullable=True)
    last_updated = Column(
        DateTime,
        server_default=text("SYSDATETIME()"),
        nullable=True,
    )

    

    def __repr__(self):
        values = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        return f"<CrmTicket {values}>"



