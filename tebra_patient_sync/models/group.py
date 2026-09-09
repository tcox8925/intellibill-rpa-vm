"""SQLAlchemy ORM model for "EDI_Tebra"."group", mirroring
migrations/client_and_group.sql.

Named Group - the table itself is named "group" (a reserved word,
quoted in the SQL); __tablename__ carries the real name.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, text
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class Group(Base):
    __tablename__ = "group"
    __table_args__ = {"schema": "EDI_Tebra"}

    # --- identity ---
    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[Optional[int]] = mapped_column(ForeignKey("EDI_Tebra.client.client_id"))
    entity_id: Mapped[Optional[str]]

    # --- group / tax identifiers ---
    grp_name: Mapped[Optional[str]]
    grp_taxid: Mapped[Optional[str]]
    grp_npi: Mapped[Optional[str]]
    grp_ptan: Mapped[Optional[str]]
    taxonomy: Mapped[Optional[str]]
    pecos: Mapped[Optional[str]]
    npn: Mapped[Optional[str]]
    medicaid: Mapped[Optional[str]]
    ptan: Mapped[Optional[str]]

    # --- address ---
    grp_addr1: Mapped[Optional[str]]
    grp_addr2: Mapped[Optional[str]]
    grp_city: Mapped[Optional[str]]
    grp_st: Mapped[Optional[str]]
    grp_zip: Mapped[Optional[str]]

    # --- contact ---
    grp_contact_lnam: Mapped[Optional[str]]
    grp_contact_fnam: Mapped[Optional[str]]
    grp_contact_email: Mapped[Optional[str]]
    grp_contact_number: Mapped[Optional[str]]

    # --- review / risk ---
    is_manual_review_on: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    denial_risk_threshold: Mapped[Optional[int]] = mapped_column(default=30, server_default=text("30"))

    # --- audit ---
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
