from semantic_registry import MODULES, BLOCKED_KEYWORDS, GLOBAL_ENTITIES
import re


def validate_sql(sql: str) -> None:

    normalized = sql.upper().strip()

    # -------------------------------------------------
    # 1️⃣ ONLY SELECT
    # -------------------------------------------------
    if not normalized.startswith("SELECT"):
        raise ValueError("Only SELECT statements allowed")

    # -------------------------------------------------
    # 2️⃣ BLOCKED KEYWORDS
    # -------------------------------------------------
    for keyword in BLOCKED_KEYWORDS:
        if re.search(rf"\b{keyword}\b", normalized):
            raise ValueError(f"Blocked keyword detected: {keyword}")

    # -------------------------------------------------
    # 3️⃣ MULTI-STATEMENT BLOCK
    # -------------------------------------------------
    if ";" in sql.strip()[:-1]:
        raise ValueError("Multiple statements not allowed")

    # -------------------------------------------------
    # 4️⃣ ALLOWED TABLES (MODULES + GLOBAL ENTITIES)
    # -------------------------------------------------

    allowed_tables = {
        m["table"].upper() for m in MODULES.values()
    }.union({
        e["table"].upper() for e in GLOBAL_ENTITIES.values()
    })

    # Extract tables after FROM or JOIN
    table_matches = re.findall(
        r"\bFROM\s+([A-Z0-9_\.\[\]]+)", normalized
    )
    table_matches += re.findall(
        r"\bJOIN\s+([A-Z0-9_\.\[\]]+)", normalized
    )

    if not table_matches:
        raise ValueError("No table reference found")

    for table in table_matches:
        table_name = table.strip().rstrip(",")

        if table_name not in allowed_tables:
            raise ValueError(f"Query references unauthorized table: {table_name}")