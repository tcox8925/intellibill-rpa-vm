"""
Unit tests for Jay SQL security and format engine.

All tests are pure unit tests - no database or LLM calls required.
Run with: pytest tests/test_jay_sql.py -v
"""

import pytest

from app.utils.jay.sql_validator import validate_sql, SQLValidationError
from app.utils.jay.format_engine import decide_format, compute_sql_fingerprint


# ===================================================================
# SQL Validator Tests
# ===================================================================


class TestSQLValidatorBlocks:
    """Tests that validate_sql correctly blocks dangerous SQL."""

    def test_blocks_insert(self):
        with pytest.raises(SQLValidationError):
            validate_sql("INSERT INTO wpo.lup_agents (npn) VALUES ('123')")

    def test_blocks_update(self):
        with pytest.raises(SQLValidationError):
            validate_sql("UPDATE wpo.lup_agents SET npn = '999' WHERE id = 1")

    def test_blocks_delete(self):
        with pytest.raises(SQLValidationError):
            validate_sql("DELETE FROM wpo.lup_agents WHERE id = 1")

    def test_blocks_drop(self):
        with pytest.raises(SQLValidationError):
            validate_sql("DROP TABLE wpo.lup_agents")

    def test_blocks_semicolon_injection(self):
        with pytest.raises(SQLValidationError):
            validate_sql("SELECT 1; DROP TABLE wpo.lup_agents")


class TestSQLValidatorAllows:
    """Tests that validate_sql correctly allows safe SQL."""

    def test_allows_valid_select(self):
        # Should not raise
        validate_sql("SELECT COUNT(*) FROM wpo.lup_agents t")

    def test_blocks_unauthorized_table(self):
        with pytest.raises(SQLValidationError, match="unauthorized table"):
            validate_sql("SELECT * FROM secret_table t")

    def test_allows_module_table(self):
        # wpo.lup_agents is in the MODULES registry (agents module)
        validate_sql("SELECT COUNT(*) FROM wpo.lup_agents t LIMIT 50")


# ===================================================================
# Format Engine Tests
# ===================================================================


class TestDecideFormat:
    """Tests for decide_format return values."""

    def test_single_value_returns_kpi(self):
        result = decide_format([{"value": 42}])
        assert result["format"] == "kpi"

    def test_grouped_data_returns_bar_chart(self):
        data = [
            {"group_name": f"Group {i}", "value": i * 10}
            for i in range(5)
        ]
        result = decide_format(data, dimensions=["group_name"])
        assert result["format"] == "bar_chart"

    def test_many_groups_returns_table(self):
        data = [
            {"group_name": f"Group {i}", "value": i * 10}
            for i in range(15)
        ]
        result = decide_format(data, dimensions=["group_name"])
        assert result["format"] == "table"

    def test_list_records_returns_table(self):
        data = [
            {"npn": f"NPN{i}", "name": f"Agent {i}", "email": f"a{i}@x.com"}
            for i in range(10)
        ]
        result = decide_format(data, metric="list_records")
        assert result["format"] == "table"

    def test_empty_data_returns_text(self):
        result = decide_format([])
        assert result["format"] == "text"


class TestSQLFingerprint:
    """Tests for compute_sql_fingerprint stability and uniqueness."""

    def test_sql_fingerprint_stable(self):
        sql = "SELECT COUNT(*) FROM wpo.lup_agents t LIMIT 50"
        fp1 = compute_sql_fingerprint(sql, [], "agents")
        fp2 = compute_sql_fingerprint(sql, [], "agents")
        assert fp1 == fp2

    def test_sql_fingerprint_differs(self):
        sql_a = "SELECT COUNT(*) FROM wpo.lup_agents t LIMIT 50"
        sql_b = "SELECT COUNT(DISTINCT npn) FROM wpo.lup_agents t LIMIT 50"
        fp_a = compute_sql_fingerprint(sql_a, [], "agents")
        fp_b = compute_sql_fingerprint(sql_b, [], "agents")
        assert fp_a != fp_b


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
