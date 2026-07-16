from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, Integer
from sqlalchemy.dialects.postgresql import JSONB
from app.models.BaseClasses import Base


class JaySchemaMetadata(Base):
    __tablename__ = "jay_schema_metadata"
    __table_args__ = {"schema": "wpo"}

    id = Column(Integer, primary_key=True, autoincrement=True)
    module_name = Column(String(50), unique=True, nullable=False, index=True)
    registry_json = Column(JSONB, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self):
        return (
            f"<JaySchemaMetadata(id={self.id}, module_name='{self.module_name}', "
            f"updated_at='{self.updated_at}')>"
        )
