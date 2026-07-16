"""
Unit tests for Jay API models and format_engine edge cases.

All tests are pure unit tests - no database or LLM calls required.
Run with: pytest tests/test_jay.py -v
"""

import sys
from types import ModuleType
from unittest.mock import MagicMock

# Mock azure SDK and pydantic_settings before they are imported by
# app.core.config (which is pulled in transitively via app.models.__init__).
# app.core.config calls Azure Key Vault at module level, so we must
# intercept the azure imports so that config.py can execute without
# real Azure credentials.
_azure_mods = [
    "azure",
    "azure.identity",
    "azure.keyvault",
    "azure.keyvault.secrets",
    "azure.storage",
    "azure.storage.blob",
    "azure.core",
    "azure.core.exceptions",
]
for _name in _azure_mods:
    if _name not in sys.modules:
        _m = MagicMock()
        # Prevent spec-related issues: give it module-like attributes
        _m.__name__ = _name
        _m.__path__ = []
        _m.__package__ = _name
        _m.__file__ = f"<mock {_name}>"
        _m.__all__ = []
        sys.modules[_name] = _m

# pydantic_settings may also be missing in some local envs
if "pydantic_settings" not in sys.modules:
    sys.modules["pydantic_settings"] = MagicMock()

import pytest

from app.models.jay.JayConversation import JayConversation
from app.models.jay.JayMessage import JayMessage
from app.models.jay.JayFavorite import JayFavorite
from app.utils.jay.format_engine import decide_format


# ===================================================================
# Model Schema Tests (no live DB needed - inspecting class attributes)
# ===================================================================


class TestJayConversationModel:
    """Tests that JayConversation model has correct table name and schema."""

    def test_table_name(self):
        assert JayConversation.__tablename__ == "jay_conversations"

    def test_schema(self):
        assert JayConversation.__table_args__ == {"schema": "wpo"}

    def test_has_user_id_column(self):
        col = JayConversation.__table__.columns["user_id"]
        assert col is not None
        assert col.nullable is False

    def test_has_title_column(self):
        col = JayConversation.__table__.columns["title"]
        assert col is not None

    def test_has_messages_relationship(self):
        # Verify the relationship is defined on the mapper
        mapper = JayConversation.__mapper__
        assert "messages" in mapper.relationships


class TestJayMessageModel:
    """Tests that JayMessage model has cascade delete FK."""

    def test_table_name(self):
        assert JayMessage.__tablename__ == "jay_messages"

    def test_schema(self):
        assert JayMessage.__table_args__ == {"schema": "wpo"}

    def test_conversation_id_fk_cascade(self):
        col = JayMessage.__table__.columns["conversation_id"]
        assert col is not None
        assert col.nullable is False

        # Check foreign key has CASCADE ondelete
        fk = list(col.foreign_keys)[0]
        assert fk.ondelete == "CASCADE"

    def test_has_role_column(self):
        col = JayMessage.__table__.columns["role"]
        assert col is not None
        assert col.nullable is False

    def test_has_data_column(self):
        col = JayMessage.__table__.columns["data"]
        assert col is not None
        assert col.nullable is True


class TestJayFavoriteModel:
    """Tests that JayFavorite model has user_id index."""

    def test_table_name(self):
        assert JayFavorite.__tablename__ == "jay_favorites"

    def test_schema(self):
        assert JayFavorite.__table_args__ == {"schema": "wpo"}

    def test_user_id_is_indexed(self):
        col = JayFavorite.__table__.columns["user_id"]
        assert col is not None
        assert col.index is True

    def test_has_prompt_text_column(self):
        col = JayFavorite.__table__.columns["prompt_text"]
        assert col is not None
        assert col.nullable is False

    def test_has_use_count_column(self):
        col = JayFavorite.__table__.columns["use_count"]
        assert col is not None


# ===================================================================
# Format Engine Edge Cases
# ===================================================================


class TestFormatEngineEdgeCases:
    """Edge case tests for decide_format."""

    def test_two_dimensions_returns_table(self):
        """2+ dimensions -> always table."""
        data = [
            {"state": "TX", "carrier": "Aetna", "value": 100},
            {"state": "CA", "carrier": "BCBS", "value": 200},
        ]
        result = decide_format(
            data,
            dimensions=["state", "carrier"],
            metric="count_records",
        )
        assert result["format"] == "table"

    def test_over_200_rows_returns_table_with_total_rows(self):
        """> 200 rows -> table with total_rows reflecting actual count."""
        data = [
            {"name": f"Agent {i}", "value": i}
            for i in range(250)
        ]
        result = decide_format(data, metric="list_records")
        assert result["format"] == "table"
        assert result["data"]["total_rows"] == 250
        # Rows should be truncated to MAX_INLINE_ROWS (200)
        assert len(result["data"]["rows"]) == 200

    def test_format_hint_pie_chart_respected(self):
        """format_hint 'pie_chart' respected for small groups."""
        data = [
            {"carrier": "Aetna", "value": 100},
            {"carrier": "BCBS", "value": 200},
            {"carrier": "Cigna", "value": 150},
        ]
        result = decide_format(
            data,
            format_hint="pie_chart",
            dimensions=["carrier"],
        )
        assert result["format"] == "pie_chart"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
