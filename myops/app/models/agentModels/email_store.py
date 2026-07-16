import json
from app.models.BaseClasses import Base
from sqlalchemy import TIMESTAMP, Column, String, JSON
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
from sqlalchemy.dialects.postgresql import UUID
import uuid


class EmailStore(Base):
    __tablename__ = "email_store"
    __table_args__ = {"schema": "wpo"}


    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4, nullable=False)
    
    sender = Column(String(320), nullable=False)
    recipients = Column(String, nullable=False)  
    cc = Column(String, nullable=True)  
    bcc = Column(String, nullable=True)  
    subject = Column(String(500), nullable=True)
    body = Column(String, nullable=True)
    body_format = Column(String(20), nullable=False, default='plain') 
    email_type = Column(String(50), nullable=True)
    sent_datetime = Column(TIMESTAMP(timezone=True), nullable=True)
    schedule_datetime = Column(TIMESTAMP(timezone=True), nullable=True)
    attachments = Column(JSON, nullable=True)
    status = Column(String(100), nullable=True, server_default='pending')
    source_id = Column(UUID(as_uuid=True), nullable=True)
        
    
    def __repr__(self):
        values = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        return f"<EmailStore {values}>"
    
class TicketsEmailStore(Base):
    __tablename__ = "tickets_email_store"
    __table_args__ = {"schema": "wpo"}


    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4, nullable=False)
    
    sender = Column(String(320), nullable=False)
    recipients = Column(String, nullable=False)  
    cc = Column(String, nullable=True)  
    bcc = Column(String, nullable=True)  
    subject = Column(String(500), nullable=True)
    body = Column(String, nullable=True)
    body_format = Column(String(20), nullable=False, default='plain') 
    email_type = Column(String(50), nullable=True)
    sent_datetime = Column(TIMESTAMP(timezone=True), nullable=True)
    schedule_datetime = Column(TIMESTAMP(timezone=True), nullable=True)
    attachments = Column(JSON, nullable=True)
    status = Column(String(100), nullable=True, server_default='pending')
    ticket_id = Column(UUID(as_uuid=True), nullable=False)
        
    
    def __repr__(self):
        values = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        return f"<TicketsEmailStore {values}>"