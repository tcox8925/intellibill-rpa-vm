from sqlalchemy import Column
from sqlalchemy.dialects.mssql import VARCHAR
from app.models.BaseClasses import Base


class pch_attachments(Base):
    __tablename__ = "pch_attachments"
    __table_args__ = {"schema": "wpo"}

    txn_id = Column(VARCHAR(50), primary_key=True, nullable=False)
    path = Column(VARCHAR(200), nullable=False)
    description = Column(VARCHAR(300), nullable=True)
    date_time = Column(VARCHAR(50), nullable=True)
    login = Column(VARCHAR(50), nullable=True)
    owner_name = Column(VARCHAR(50), nullable=True)
    txn_id_provider = Column(VARCHAR(50), nullable=True)
    file_type = Column(VARCHAR(30), nullable=True)

    def __repr__(self):
        return f"<pch_attachments(txn_id={self.txn_id}, path={self.path}, login={self.login})>"
