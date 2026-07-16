from sqlalchemy import Column, String
from app.models.BaseClasses import Base 
import uuid
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER

class LupUsers(Base):
    __tablename__ = "lup_users"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(
        UNIQUEIDENTIFIER,
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )

    id = Column(String, primary_key=True, nullable=True)
    full_name = Column(String, nullable=True)
    email = Column(String, nullable=True)
    modified_time = Column(String, nullable=True)
