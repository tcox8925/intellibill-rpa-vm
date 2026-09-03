"""SQLAlchemy ORM model for "EDI_Tebra".client, mirroring
migrations/client_and_group.sql.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class Client(Base):
    __tablename__ = "client"
    __table_args__ = (
        CheckConstraint(
            "denial_risk_threshold >= 0 AND denial_risk_threshold <= 100",
            name="client_denial_risk_threshold_chk",
        ),
        {"schema": "EDI_Tebra"},
    )

    # --- identity ---
    client_id: Mapped[int] = mapped_column(primary_key=True)
    client_name: Mapped[Optional[str]]
    client_status: Mapped[Optional[str]]
    client_taxid: Mapped[Optional[str]]

    # --- contact ---
    client_contact_lnam: Mapped[Optional[str]]
    client_contact_fnam: Mapped[Optional[str]]
    client_contact_email: Mapped[Optional[str]]
    client_contact_number: Mapped[Optional[str]]

    # --- address ---
    client_addr1: Mapped[Optional[str]]
    client_addr2: Mapped[Optional[str]]
    client_city: Mapped[Optional[str]]
    client_state: Mapped[Optional[str]]
    client_zip: Mapped[Optional[str]]

    # --- branding ---
    client_logo: Mapped[Optional[str]]

    # --- risk / status ---
    denial_risk_threshold: Mapped[int] = mapped_column(default=30, server_default=text("30"))
    status: Mapped[bool] = mapped_column(default=True, server_default=text("true"))

    # --- audit ---
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
