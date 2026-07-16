from sqlalchemy import Column, BigInteger, VARCHAR, TIMESTAMP, text
from app.models.BaseClasses import Base


class Pch_Networks(Base):
    __tablename__ = "pch_networks"
    __table_args__ = {"schema": "wpo"}

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    pk_id = Column(VARCHAR(50), nullable=False)
    provider = Column(VARCHAR(10), nullable=False)
    affiliation = Column(VARCHAR(12), nullable=False)
    status = Column(VARCHAR(8), nullable=False)
    created_at = Column(
        TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at = Column(
        TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    def __repr__(self):
        return (
            f"<PchNetworks(id={self.id}, provider={self.provider}, "
            f"affiliation={self.affiliation}, status={self.status})>"
        )
