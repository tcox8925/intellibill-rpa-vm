import uuid
from sqlalchemy import Column, Text, text
from sqlalchemy.dialects.postgresql import UUID
from app.models.BaseClasses import Base

class ComTotals(Base):
    __tablename__ = "com_totals"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"), nullable=False)
    job_id = Column(Text, nullable=True)
    company_id = Column(Text)
    company_name = Column(Text)
    carrier_id = Column(Text)
    carrier_name = Column(Text)
    statement_month = Column(Text)
    npn = Column(Text)
    agent_name = Column(Text)
    payment_type = Column(Text)
    associated_statement = Column(Text)
    statement_total = Column(Text)  # Changed from Float since DB uses text
    status = Column(Text)
    status_date = Column(Text)  # Changed from DateTime since DB uses text
    report_date = Column(Text)  # Changed from DateTime since DB uses text
    load_date = Column(Text)    # Added missing column from DB schema
    source = Column(Text)

    def __repr__(self):
        values = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        return f"<ComTotals {values}>"