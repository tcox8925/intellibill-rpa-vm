"""
JAI Configuration models — DB-driven business knowledge tables.

These tables store synonyms, glossary entries, filter values, and query examples
that were previously hardcoded in Python dicts. Managed via admin UI.
"""

from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, UniqueConstraint
from app.models.BaseClasses import Base


class JaySynonym(Base):
    __tablename__ = "jay_config_synonyms"
    __table_args__ = (
        UniqueConstraint("term", name="uq_jay_synonyms_term"),
        {"schema": "wpo"},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    term = Column(String(200), nullable=False, index=True)
    db_column = Column(String(200), nullable=False)
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class JayGlossaryEntry(Base):
    __tablename__ = "jay_config_glossary"
    __table_args__ = (
        UniqueConstraint("term", name="uq_jay_glossary_term"),
        {"schema": "wpo"},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    term = Column(String(200), nullable=False, index=True)
    description = Column(Text, nullable=True)
    sql_hint = Column(Text, nullable=True)
    module = Column(String(100), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class JayFilterValue(Base):
    __tablename__ = "jay_config_filter_values"
    __table_args__ = (
        UniqueConstraint(
            "module", "filter_name", "valid_value",
            name="uq_jay_filter_values_module_filter_value",
        ),
        {"schema": "wpo"},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    module = Column(String(100), nullable=False, index=True)
    filter_name = Column(String(100), nullable=False)
    column_name = Column(String(100), nullable=False)
    filter_type = Column(String(50), nullable=True)
    valid_value = Column(String(200), nullable=False)
    sort_order = Column(Integer, nullable=True, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class JayQueryExample(Base):
    __tablename__ = "jay_config_query_examples"
    __table_args__ = (
        UniqueConstraint(
            "name", "example_type",
            name="uq_jay_query_examples_name_type",
        ),
        {"schema": "wpo"},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False, index=True)
    example_type = Column(String(50), nullable=False, default="sql_example")
    sql_text = Column(Text, nullable=True)
    natural_query = Column(Text, nullable=True)
    domain_tags = Column(Text, nullable=True)
    module_tag = Column(String(100), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
