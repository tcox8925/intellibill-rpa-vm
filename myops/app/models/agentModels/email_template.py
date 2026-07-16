from app.models.BaseClasses import Base
from sqlalchemy import Column, String, ForeignKey
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
from sqlalchemy.dialects.postgresql import UUID
import uuid


class EmailTemplate(Base):
    __tablename__ = "email_template"
    __table_args__ = {"schema": "wpo"}


    template_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    
    template = Column(String, nullable=True)
    template_name = Column(String, nullable=True)
    template_category = Column(String, nullable=True)
    template_module = Column(String, nullable=True)
    template_design = Column(String, nullable=True)
    entity_id = Column(String, nullable=True)
    sub_entity_id = Column(String, nullable=True)
    owner = Column(UUID(as_uuid=True), ForeignKey('ops_sec.users.user_id'), nullable=True)

    def __repr__(self):
        values = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        return f"<EmailTemplate {values}>"