"""SQLAlchemy ORM model for "EDI_Tebra".lookup_payers, mirroring
migrations/lookup_payers.sql.

payer_type / transaction_type / payer_alias are Postgres text[] (_text)
columns - mapped as ARRAY(String); a NULL column round-trips as None, so
callers that need an iterable (as utils/payer_lookup.find_payer does) use
`or []`.
"""

from typing import Any, Optional

from sqlalchemy import String, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class LookupPayer(Base):
    __tablename__ = "lookup_payers"
    __table_args__ = {"schema": "EDI_Tebra"}

    # --- identity ---
    id: Mapped[int] = mapped_column(primary_key=True)  # serial4 PK - unset until the DB assigns it
    sort_id: Mapped[Optional[int]] = mapped_column(default=None)  # bigserial, unique - DB-assigned

    # --- payer matching (see utils/payer_lookup.find_payer) ---
    payer_name: Mapped[Optional[str]] = mapped_column(default=None)
    payer_id: Mapped[Optional[str]] = mapped_column(default=None)
    payer_alias: Mapped[Optional[list[str]]] = mapped_column(ARRAY(String), default=None)
    payer_type: Mapped[Optional[list[str]]] = mapped_column(ARRAY(String), default=None)
    transaction_type: Mapped[Optional[list[str]]] = mapped_column(ARRAY(String), default=None)

    # --- claim submission details ---
    available: Mapped[Optional[str]] = mapped_column(default=None)
    non_par: Mapped[Optional[str]] = mapped_column(default=None)
    enrollment: Mapped[Optional[str]] = mapped_column(default=None)
    secondary: Mapped[Optional[str]] = mapped_column(default=None)
    attachment: Mapped[Optional[str]] = mapped_column(default=None)
    wc_auto: Mapped[Optional[str]] = mapped_column(default=None)
    notes: Mapped[Optional[str]] = mapped_column(default=None)

    # --- portal credentials ---
    portal: Mapped[Optional[str]] = mapped_column(default=None)
    login: Mapped[Optional[str]] = mapped_column(default=None)
    password: Mapped[Optional[str]] = mapped_column(default=None)

    # --- status / integration ---
    active_status: Mapped[Optional[bool]] = mapped_column(default=True, server_default=text("true"))
    integration_details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, default=None)
