from sqlalchemy import Column, Integer, NVARCHAR, Boolean, VARCHAR
from app.models.BaseClasses import Base  # adjust import as per your project

class Pch_Notes(Base):
    __tablename__ = "pch_notes"
    __table_args__ = {"schema": "wpo"}

    txn_id = Column(VARCHAR(50), primary_key=True, nullable=True)
    note_type = Column(VARCHAR(50), nullable=True)
    notes_context = Column(NVARCHAR(None), nullable=True)
    date_time = Column(VARCHAR(50), nullable=True)
    login = Column(VARCHAR(50), nullable=True)
    reference_id = Column(VARCHAR(50), nullable=True)
    module = Column(VARCHAR(150), nullable=True)

    def __repr__(self):
        return (
            f"<PchNotes(txn_id={self.txn_id}, note_type={self.note_type}, "
            f"date_time={self.date_time}, login={self.login})>"
        )