import uuid
from sqlalchemy import Column
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER, VARCHAR
from app.models.BaseClasses import Base

class UserRoles(Base):
    __tablename__ = "user_roles"
    __schema__ = "ops_sec"
    __table_args__ = {'schema': __schema__}

    role_id = Column(UNIQUEIDENTIFIER, primary_key=True, default=uuid.uuid4)
    role_name = Column(VARCHAR(100))
    role_code = Column(VARCHAR(50))

    def __repr__(self):
        return (
            f"""<UserRoles(
            role_id={self.role_id}, 
            role_name={self.role_name}
            role_code='{self.role_code}'
            )>"""
        )