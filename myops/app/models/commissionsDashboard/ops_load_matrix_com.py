from sqlalchemy import Column, Text, String
from sqlalchemy.dialects.postgresql import UUID
from app.models.BaseClasses import Base
from sqlalchemy.sql import func
import uuid

class OpsLoadMatrixCom(Base):
    __tablename__ = "ops_load_matrix_com"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    process_type = Column(Text)
    company_id = Column(Text)
    company_name = Column(Text)
    carrier_name = Column(Text)
    carrier_id = Column(Text)
    raw_file_name_prefix = Column(Text)
    raw_file_required = Column(Text)
    file_extension = Column(Text)
    sheet_name = Column(Text)
    skip_rows = Column(Text)
    contract_count = Column(Text)
    contract_type = Column(Text)
    has_headers = Column(Text)
    database_column = Column(Text)
    mapping = Column("mapping", Text)  # mapped column name
    fill_na = Column(Text)
    start_date = Column(Text)
    end_date = Column(Text)
    load_date = Column(Text)

    def __repr__(self):
        return (
            f"<OpsLoadMatrixCom(pk_id={self.pk_id}, process_type={self.process_type}, "
            f"company_id={self.company_id}, carrier_id={self.carrier_id}, "
            f"raw_file_name_prefix={self.raw_file_name_prefix})>"
        )
