from sqlalchemy import Column, Integer, String, Float, Date, Numeric, Text, JSON, ForeignKey, DateTime, VARCHAR, text
from sqlalchemy.dialects.postgresql import UUID
from app.models.BaseClasses import Base
from sqlalchemy import TIMESTAMP, Column, String, JSON
import uuid

class ProjectStatus(Base):
    __tablename__ = 'project_status'
    __schema__ = "ops_srv"
    __table_args__ = {'schema': __schema__}

    pk_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(Integer)
    project_name = Column(Text)
    phase = Column(Text)
    lead = Column(Text)
    project_status = Column(Text)
    progress = Column(Float)
    priority = Column(Text)
    major_deliverable = Column(Text)
    project_date = Column(Date)
    requested = Column(Text)
    project_type = Column(Text)
    attachments = Column(JSON)
    entity_id = Column(Text)
    buisness_entity = Column(String(20), nullable=True)
    created_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True))
    project_cost = Column(Numeric, default=0)

    def __repr__(self):
        return f"<ProjectStatus(pk_id={self.pk_id}, project_name={self.project_name})>"
    
class ProjectFeature(Base):
    __tablename__ = 'project_status_deliverables'
    __schema__ = "ops_srv"
    __table_args__ = {'schema': __schema__}

    pk_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_pk_id = Column(UUID(as_uuid=True), ForeignKey(f"{__schema__}.project_status.pk_id"))
    category = Column(Text)
    features = Column(Text)
    go_live_date = Column(Date, nullable=True)
    status = Column(Text)
    priority_number = Column(Numeric)
    project_name = Column(String(200))
    phase = Column(VARCHAR(30))
    progress = Column(VARCHAR(50))
    push_count = Column(Integer, default=0)

    def __repr__(self):
        return f"<ProjectFeature(pk_id={self.pk_id}, project_pk_id={self.project_pk_id}, project_name={self.project_name})>"
    
class ProjectSubTask(Base):
    __tablename__ = 'project_status_sub_tasks'
    __schema__ = "ops_srv"
    __table_args__ = {'schema': __schema__}

    pk_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    feature_pk_id = Column(UUID(as_uuid=True), ForeignKey(f"{__schema__}.project_status_deliverables.pk_id"))
    id = Column(
        Text,
        nullable=False,
        server_default=text("'TCK-' || nextval('ops_srv.project_status_sub_tasks_id_seq')")
    )
    title = Column(Text)
    status = Column(Text)
    priority_number = Column(Numeric)
    assignee = Column(Text)
    description = Column(Text)

    def __repr__(self):
        return f"<ProjectSubTask(pk_id={self.pk_id}, deliverable_pk_id={self.deliverable_pk_id}, title={self.title})>"

class ProjectResource(Base):
    __tablename__ = 'project_status_resources'
    __schema__ = "ops_srv"
    __table_args__ = {'schema': __schema__}
    pk_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey(f"{__schema__}.project_status.pk_id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("ops_sec.users.user_id"), nullable=False)
    role = Column(String(100))

    def __repr__(self):
        return f"<ProjectResource(pk_id={self.pk_id}, project_id={self.project_id}, user_id={self.user_id}, role={self.role})>"
class ProjectStatusAuditHistory(Base):
    __tablename__ = 'project_status_audit_history'
    __schema__ = "ops_srv"
    __table_args__ = {'schema': __schema__}
    
    pk_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = Column(DateTime(timezone=True))
    user_id = Column(UUID(as_uuid=True), nullable=False)
    project_id = Column(UUID(as_uuid=True), nullable=False)
    action_message = Column(Text)
    action = Column(VARCHAR(50))
    tab = Column(VARCHAR(30))
    sub_entity_id = Column(VARCHAR(30))
    entity_id = Column(VARCHAR(30), nullable=False)

    def __repr__(self):
        return f"<ProjectStatusAuditHistory(pk_id={self.pk_id}, project_id={self.project_id}, action={self.action})>"
    

class ProjectStatusNotes(Base):
    __tablename__ = "project_status_notes"
    __schema__ = "ops_srv"
    __table_args__ = {"schema": __schema__}

    pk_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    module = Column(String(50), nullable=False)
    description = Column(Text, nullable=False)
    time_stamp = Column(
        DateTime(timezone=True),
        server_default=text("TIMEZONE('UTC', CURRENT_TIMESTAMP)"),
        nullable=True,
    )
    user_id = Column(UUID(as_uuid=True), ForeignKey("ops_sec.users.user_id"), nullable=True)
    source_id = Column(UUID(as_uuid=True), nullable=True)

    def __repr__(self):
        return f"<ProjectStatusNotes(pk_id={self.pk_id}, module={self.module}, user_id={self.user_id})>"


class ProjectStatusEmailStore(Base):
    __tablename__ = "project_status_email_store"
    __table_args__ = {"schema": "ops_srv"}


    pk_id = Column(UUID, primary_key=True, default=uuid.uuid4, nullable=False)
    
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
        return f"<ProjectStatusEmailStore {values}>"