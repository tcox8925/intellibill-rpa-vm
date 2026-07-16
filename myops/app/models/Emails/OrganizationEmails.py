from app.models.BaseClasses import Base
import uuid
from sqlalchemy import Column, String
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER

class OrganizationEmails(Base):
    __tablename__ = "lup_organization_emails"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    email = Column(String(255), nullable=False)
    department = Column(String(200), nullable=True)
    type = Column(String(100), nullable=True)

    def __repr__(self):
        return f"<LupOrganizationEmails(email='{self.email}', department='{self.department}', type='{self.type}')>"
