from sqlalchemy import Column, NVARCHAR, DateTime
from app.models.BaseClasses import Base


class pch_carriers(Base):
    __tablename__ = "pch_carriers"
    __table_args__ = {"schema": "wpo"}

    txn_id = Column(NVARCHAR(None), primary_key=True, nullable=True)
    carrier_name = Column(NVARCHAR(None), nullable=True)
    carrier_niac_number = Column(NVARCHAR(None), nullable=True)
    network_status = Column(NVARCHAR(None), nullable=True)
    txn_id_provider = Column(NVARCHAR(None), nullable=True)
    updated_on = Column(DateTime, nullable=True)
    source = Column(NVARCHAR(None), nullable=True)

    def __repr__(self):
        return (
            f"<PchCarriers("
            f"txn_id='{self.txn_id}', "
            f"carrier_name='{self.carrier_name}', "
            f"carrier_niac_number='{self.carrier_niac_number}', "
            f"network_status='{self.network_status}', "
            f"txn_id_provider='{self.txn_id_provider}', "
            f"updated_on='{self.updated_on}', "
            f"source='{self.source}'"
            f")>"
        )
