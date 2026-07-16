from sqlalchemy import Column, BigInteger, String, Integer, DateTime, UniqueConstraint
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER
from sqlalchemy.sql import func
import uuid

from app.models.BaseClasses import Base  # adjust import path based on your project


class CallRecording(Base):
    __tablename__ = "five9_search_results"
    __table_args__ = (
        UniqueConstraint("id", name="UQ_five9_search_results_id"),
        {"schema": "wpo"},
    )

    pk_id = Column(
        UNIQUEIDENTIFIER,
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    id = Column(BigInteger, nullable=False, unique=True)
    username = Column(String(200), nullable=True)
    date = Column(String(20), nullable=True)
    result_directory = Column(String(500), nullable=True)
    file_cnt = Column(Integer, nullable=True)
    created_on = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(50), nullable=True)
