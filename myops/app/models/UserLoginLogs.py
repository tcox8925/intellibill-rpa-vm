import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER, NVARCHAR
from app.models.BaseClasses import Base

class UserLoginLogs(Base):
    __tablename__ = "user_login_logs"
    __schema__ = "ops_sec"
    __table_args__ = {'schema': __schema__}

    log_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    email = Column(NVARCHAR(255), nullable=True)
    ip_address = Column(NVARCHAR(45), nullable=True)
    login_time = Column(DateTime, nullable=False, default=datetime.utcnow)
    action = Column(String(10), nullable=False)
    reason = Column(NVARCHAR(255), nullable=True)

    def __repr__(self):
        return f"<UserLoginLog(email={self.email}, ip={self.ip_address}, action={self.action}, login_time={self.login_time})>"
