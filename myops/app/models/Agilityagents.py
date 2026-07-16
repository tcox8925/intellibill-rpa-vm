from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class LupAgent(Base):
    __tablename__ = "lup_agents"
    __table_args__ = {"schema": "wpo"}

    npn: Mapped[str] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(nullable=True)
    email: Mapped[str] = mapped_column(nullable=True)
