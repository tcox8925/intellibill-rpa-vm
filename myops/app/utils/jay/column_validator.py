"""
Column Validation Gate — catches hallucinated columns before DB execution.

Parses generated SQL, extracts column references, and verifies them against
a known column inventory (from DDL).  If invalid columns are found, attempts
fuzzy correction via difflib.  Runs in < 5 ms on typical queries.
"""

import logging
import re
from dataclasses import dataclass, field
from difflib import get_close_matches
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# SQL keywords that look like column refs but aren't
_SQL_KEYWORDS = frozenset({
    "select", "from", "where", "join", "inner", "left", "right", "outer",
    "full", "cross", "on", "and", "or", "not", "in", "is", "null", "as",
    "group", "by", "order", "having", "limit", "offset", "union", "all",
    "distinct", "case", "when", "then", "else", "end", "between", "like",
    "ilike", "exists", "asc", "desc", "cast", "coalesce", "nullif", "count",
    "sum", "avg", "min", "max", "true", "false", "with", "recursive",
    "insert", "update", "delete", "set", "values", "into", "create", "table",
    "drop", "alter", "index", "over", "partition", "row_number", "rank",
    "dense_rank", "lag", "lead", "first_value", "last_value", "extract",
    "interval", "date", "timestamp", "varchar", "integer", "numeric",
    "boolean", "text", "bigint", "smallint", "float", "double", "precision",
    "trim", "lower", "upper", "substring", "length", "replace", "concat",
    "top",
})

# Pattern: alias.column_name (qualified column reference)
_QUALIFIED_COL_RE = re.compile(
    r'\b([a-zA-Z_]\w*)\.([a-zA-Z_]\w*)\b'
)

# FROM / JOIN clause: captures schema.table alias patterns
_TABLE_REF_RE = re.compile(
    r'(?:FROM|JOIN)\s+'
    r'(?:(\w+)\.)?'          # optional schema
    r'(\w+)'                 # table name
    r'(?:\s+AS\s+|\s+)'     # AS or space
    r'(\w+)',                # alias
    re.IGNORECASE
)

# Simpler: FROM/JOIN schema.table (no alias, table is its own alias)
_TABLE_NO_ALIAS_RE = re.compile(
    r'(?:FROM|JOIN)\s+'
    r'(?:(\w+)\.)?'          # optional schema
    r'(\w+)'                 # table name
    r'(?:\s*(?:WHERE|ON|JOIN|LEFT|RIGHT|INNER|OUTER|CROSS|FULL|GROUP|ORDER|HAVING|LIMIT|UNION|;|\)|\s*$))',
    re.IGNORECASE
)


@dataclass
class ColumnValidationResult:
    is_valid: bool
    invalid_columns: List[Dict] = field(default_factory=list)
    suggestions: Dict[str, List[str]] = field(default_factory=dict)
    corrected_sql: Optional[str] = None


def extract_table_aliases(sql: str) -> Dict[str, str]:
    """Parse FROM/JOIN clauses to map aliases → full table names.

    Returns dict like {"t": "wpo.lup_agents", "c": "wpo.agents_contracts_view"}.
    """
    aliases = {}

    # Match: FROM/JOIN [schema.]table alias  or  FROM/JOIN [schema.]table AS alias
    for m in _TABLE_REF_RE.finditer(sql):
        schema, table, alias = m.group(1), m.group(2), m.group(3)
        # Skip if "alias" is a SQL keyword (means no alias was used)
        if alias.lower() in _SQL_KEYWORDS:
            full_name = f"{schema}.{table}" if schema else table
            aliases[table.lower()] = full_name.lower()
        else:
            full_name = f"{schema}.{table}" if schema else table
            aliases[alias.lower()] = full_name.lower()

    # Also catch bare table refs without aliases
    for m in _TABLE_NO_ALIAS_RE.finditer(sql):
        schema, table = m.group(1), m.group(2)
        full_name = f"{schema}.{table}" if schema else table
        if table.lower() not in aliases:
            aliases[table.lower()] = full_name.lower()

    return aliases


def extract_column_references(
    sql: str, aliases: Dict[str, str]
) -> List[Dict]:
    """Extract qualified column references (alias.column) from SQL.

    Returns list of {"alias": "t", "column": "agent_name", "table": "wpo.lup_agents"}.
    """
    refs = []
    seen = set()

    for m in _QUALIFIED_COL_RE.finditer(sql):
        prefix, col = m.group(1).lower(), m.group(2).lower()

        # Skip schema-qualified table refs (wpo.table_name)
        if prefix in ("wpo", "dbo", "public"):
            continue
        # Skip SQL function-like patterns
        if col in _SQL_KEYWORDS or prefix in _SQL_KEYWORDS:
            continue

        key = (prefix, col)
        if key in seen:
            continue
        seen.add(key)

        table = aliases.get(prefix)
        refs.append({"alias": prefix, "column": col, "table": table})

    return refs


def _fuzzy_match(
    invalid_col: str, known_columns: List[str], cutoff: float = 0.6
) -> List[str]:
    """Find close matches for an invalid column name.

    Prioritizes: exact substring > difflib > prefix match.
    """
    # Priority 1: one column contains the other as substring
    substring_matches = [
        k for k in known_columns
        if invalid_col in k or k in invalid_col
    ]
    if len(substring_matches) == 1:
        return substring_matches

    # Priority 2: difflib close matches (higher cutoff for fewer false positives)
    matches = get_close_matches(invalid_col, known_columns, n=3, cutoff=0.65)
    if matches:
        return matches

    # Priority 3: prefix match (at least 4 chars)
    if len(invalid_col) >= 4:
        prefix_matches = [k for k in known_columns if k.startswith(invalid_col[:4])]
        if prefix_matches:
            return prefix_matches[:3]

    # Priority 4: substring matches (may be multiple)
    if substring_matches:
        return substring_matches[:3]

    return []


def validate_columns(
    sql: str, column_inventory: Dict[str, List[str]]
) -> ColumnValidationResult:
    """Validate column references in SQL against known schema.

    Args:
        sql: Generated SQL string.
        column_inventory: {table_name: [column_names]} from DDL.
                          Keys should be lowercase (e.g. "wpo.lup_agents").

    Returns:
        ColumnValidationResult with validity status and correction info.
    """
    if not sql or not column_inventory:
        return ColumnValidationResult(is_valid=True)

    aliases = extract_table_aliases(sql)
    refs = extract_column_references(sql, aliases)

    if not refs:
        return ColumnValidationResult(is_valid=True)

    # Normalize inventory keys to lowercase
    norm_inventory = {k.lower(): [c.lower() for c in v] for k, v in column_inventory.items()}

    invalid = []
    suggestions = {}

    for ref in refs:
        table = ref["table"]
        col = ref["column"]

        if not table:
            # Can't resolve alias — skip (will be caught at execution)
            continue

        known_cols = norm_inventory.get(table)
        if known_cols is None:
            # Table not in inventory — skip
            continue

        if col not in known_cols:
            invalid.append(ref)
            matches = _fuzzy_match(col, known_cols)
            if matches:
                suggestions[col] = matches

    if not invalid:
        return ColumnValidationResult(is_valid=True)

    # Try auto-correction if every invalid column has exactly one suggestion
    corrected_sql = None
    all_single_match = all(
        len(suggestions.get(inv["column"], [])) == 1 for inv in invalid
    )

    if all_single_match and all(inv["column"] in suggestions for inv in invalid):
        corrected = sql
        for inv in invalid:
            wrong = inv["column"]
            right = suggestions[wrong][0]
            # Replace alias.wrong_col with alias.right_col (case-insensitive)
            pattern = re.compile(
                rf'\b({re.escape(inv["alias"])})\.{re.escape(wrong)}\b',
                re.IGNORECASE,
            )
            corrected = pattern.sub(rf'\1.{right}', corrected)
        corrected_sql = corrected
        logger.info(
            "Column auto-correction: %s",
            {inv["column"]: suggestions[inv["column"]][0] for inv in invalid},
        )

    logger.warning(
        "Column validation failed: %d invalid column(s): %s",
        len(invalid),
        [f'{i["alias"]}.{i["column"]}' for i in invalid],
    )

    return ColumnValidationResult(
        is_valid=False,
        invalid_columns=invalid,
        suggestions=suggestions,
        corrected_sql=corrected_sql,
    )


def build_column_inventory_from_ddl(ddl: str) -> Dict[str, List[str]]:
    """Parse DDL text into {table_name: [column_names]} inventory.

    Handles both full CREATE TABLE and compact "table_name:" formats.
    """
    inventory = {}

    # Full CREATE TABLE format
    create_re = re.compile(
        r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?'
        r'(?:"?(\w+)"?\.)?'   # schema
        r'"?(\w+)"?'          # table
        r'\s*\((.*?)\);',
        re.IGNORECASE | re.DOTALL,
    )

    for m in create_re.finditer(ddl):
        schema, table = m.group(1), m.group(2)
        full_name = f"{schema}.{table}".lower() if schema else table.lower()
        body = m.group(3)
        cols = []
        for line in body.split("\n"):
            line = line.strip().rstrip(",")
            if not line or line.upper().startswith(("PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT", "INDEX")):
                continue
            # First word is column name
            col_match = re.match(r'"?(\w+)"?\s+\w+', line)
            if col_match:
                cols.append(col_match.group(1).lower())
        if cols:
            inventory[full_name] = cols

    # Compact format: "table_name: col1 TYPE, col2 TYPE"  or  "schema.table_name:"
    compact_re = re.compile(r'^(\w+(?:\.\w+)?):\s*$', re.MULTILINE)
    for m in compact_re.finditer(ddl):
        table_name = m.group(1).lower()
        # Collect lines until next table header or end
        start = m.end()
        next_m = compact_re.search(ddl, start)
        block = ddl[start:next_m.start()] if next_m else ddl[start:]
        cols = []
        for line in block.split("\n"):
            line = line.strip()
            if not line:
                continue
            col_match = re.match(r'(\w+)\s+\w+', line)
            if col_match:
                cols.append(col_match.group(1).lower())
        if cols:
            inventory[table_name] = cols

    return inventory
