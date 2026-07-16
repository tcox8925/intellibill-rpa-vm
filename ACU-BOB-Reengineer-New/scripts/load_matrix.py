# ==========================================================
#  scripts/load_matrix.py
# ==========================================================
"""
load_matrix.py
--------------
Purpose:
    Load carrier rules and/or column mapping CSV files from
    Azure Blob Storage into PostgreSQL database tables.

Source:
    Storage account: agilitydatadev001 (ADLS Gen2)
    Blob container: agilityops
    Blob path: flat_files/
    Files:
        - acu_bob_carrier_rules.csv  → ops_srv.ops_acu_bob_rules_matrix
        - acu_bob_load_matrix.csv    → ops_srv.ops_acu_bob_load_matrix

Usage:
    # Load both carrier rules and column mapping
    python load_matrix.py --load both

    # Load carrier rules only
    python load_matrix.py --load rules

    # Load column mapping only
    python load_matrix.py --load mapping

    # Dry run (validate but don't write)
    python load_matrix.py --load both --dry-run

Design:
    - Reads CSV file(s) from Azure Blob Storage (flat_files/)
    - Validates required columns exist
    - Truncates target table and reloads (full replace)
    - Logs row counts and any issues
"""

import io
import argparse
import sys
import pandas as pd
from datetime import datetime

import os
current = os.path.dirname(os.path.realpath(__file__))
parent = os.path.dirname(current)
sys.path.append(parent)

from utils.db_utils import get_postgres_connection
from utils.azure_blob_utils import authenticate_blob_storage, DEFAULT_CONTAINER


# ==========================================================
#  CONFIGURATION
# ==========================================================
CARRIER_RULES_TABLE = "ops_srv.ops_acu_bob_rules_matrix"
COLUMN_MAPPING_TABLE = "ops_srv.ops_acu_bob_load_matrix"

# Blob paths
BLOB_BASE_PATH = "flat_files/"
RULES_BLOB_NAME = f"{BLOB_BASE_PATH}ops_acu_bob_rules_matrix.csv"
MAPPING_BLOB_NAME = f"{BLOB_BASE_PATH}ops_acu_bob_load_matrix.csv"

# Required columns for validation
REQUIRED_RULES_COLUMNS = [
    "carrier_id",
    "carrier_name",
    "process_type",
    "file_format",
    "file_naming_pattern",
    "active_flag",
]

REQUIRED_MAPPING_COLUMNS = [
    "process_type",
    "carrier_name",
    "carrier_id",
    "database_column",
    "mapping",
]


# ==========================================================
#  READ CSV FROM BLOB STORAGE
# ==========================================================
def read_csv_from_blob(
    blob_service_client,
    blob_name: str,
    container_name: str = DEFAULT_CONTAINER,
) -> pd.DataFrame:
    """
    Read a CSV file directly from Azure Blob Storage into a DataFrame.

    Parameters
    ----------
    blob_service_client : BlobServiceClient
        Authenticated blob client.
    blob_name : str
        Full blob path (e.g., 'flat_files/acu_bob_carrier_rules.csv').
    container_name : str
        Blob container name.

    Returns
    -------
    pd.DataFrame
        Parsed CSV data.

    Raises
    ------
    FileNotFoundError
        If the blob does not exist.
    """
    try:
        container_client = blob_service_client.get_container_client(container_name)
        blob_client = container_client.get_blob_client(blob_name)

        if not blob_client.exists():
            raise FileNotFoundError(f"Blob not found: {container_name}/{blob_name}")

        blob_data = blob_client.download_blob().readall()
        df = pd.read_csv(io.BytesIO(blob_data), dtype=str)

        # Strip leading ' from carrier_id (added for Excel text-mode display)
        if "carrier_id" in df.columns:
            df["carrier_id"] = df["carrier_id"].astype(str).str.lstrip("'")

        print(f"  ⬇️  Downloaded {blob_name} ({len(df)} rows, {len(df.columns)} columns)")
        return df

    except FileNotFoundError:
        raise
    except Exception as e:
        raise Exception(f"Failed to read blob {blob_name}: {e}")


# ==========================================================
#  VALIDATION
# ==========================================================
def validate_csv(df: pd.DataFrame, required_columns: list, table_name: str) -> bool:
    """
    Validate that the CSV has all required columns.

    Parameters
    ----------
    df : pd.DataFrame
        Loaded CSV data.
    required_columns : list
        Column names that must be present.
    table_name : str
        Table name for logging.

    Returns
    -------
    bool
        True if valid, False otherwise.
    """
    missing = [col for col in required_columns if col not in df.columns]
    if missing:
        print(f"❌ {table_name}: Missing required columns: {missing}")
        return False

    if df.empty:
        print(f"⚠️  {table_name}: CSV is empty (0 rows)")
        return False

    if "carrier_id" in df.columns:
        blank_ids = df["carrier_id"].isna().sum()
        if blank_ids > 0:
            print(f"⚠️  {table_name}: {blank_ids} row(s) with blank carrier_id")

    print(f"✅ {table_name}: Validation passed ({len(df)} rows, {len(df.columns)} columns)")
    return True


# ==========================================================
#  LOAD TO DATABASE
# ==========================================================
def load_csv_to_table(
    conn,
    df: pd.DataFrame,
    table_name: str,
    truncate_first: bool = True,
):
    """Load DataFrame into table using a safe staging pattern.
    Inserts into a temp table first — only truncates the real table
    after the insert succeeds, so a failed load never wipes data."""
    from psycopg2.extras import execute_values
    cur = conn.cursor()

    try:
        # Detect integer columns from DB schema so we can cast properly
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema || '.' || table_name = %s
              AND data_type IN ('integer', 'smallint', 'bigint')
        """, (table_name,))
        int_cols = {row[0] for row in cur.fetchall()}

        # Sanitize integer columns: cast to int or None
        # IMPORTANT: do NOT cast through float() first — float64 only has ~15 digits
        # of precision, which corrupts 19-digit carrier_ids (bigint)
        for col in int_cols:
            if col in df.columns:
                def _safe_int(val):
                    if val is None or (isinstance(val, str) and val.strip().lstrip("'") in ('', 'nan', 'NA', 'None')):
                        return None
                    try:
                        # Strip whitespace, leading quote, and any decimal portion
                        s = str(val).strip().lstrip("'")
                        if '.' in s:
                            s = s.split('.')[0]
                        return int(s)
                    except (ValueError, TypeError):
                        return None
                df[col] = df[col].apply(_safe_int)

        columns = list(df.columns)
        col_str = ", ".join(columns)

        # Clean up null-like strings that pandas writes to CSV: "NaN", "nan", "None", ""
        # These must become actual SQL NULL, not string literals
        _null_strings = {'NaN', 'nan', 'None', 'none', 'null', 'NULL', 'NA', ''}
        for col in df.columns:
            if col not in int_cols:  # int cols already handled by _safe_int
                df[col] = df[col].apply(lambda v: None if (pd.isna(v) or (isinstance(v, str) and v.strip() in _null_strings)) else v)

        df = df.where(pd.notnull(df), None)
        # Pandas silently converts None back to float NaN in object columns.
        # Explicitly convert at tuple level so psycopg2 sees Python None → SQL NULL.
        data = [tuple(None if pd.isna(v) else v for v in row)
                for row in df.itertuples(index=False, name=None)]

        # Stage 1: Insert into a temp table (validates data types, constraints)
        staging = f"_staging_{table_name.split('.')[-1]}"
        cur.execute(f"DROP TABLE IF EXISTS {staging}")
        cur.execute(f"CREATE TEMP TABLE {staging} (LIKE {table_name} INCLUDING DEFAULTS)")
        execute_values(cur, f"INSERT INTO {staging} ({col_str}) VALUES %s", data, page_size=500)
        print(f"  ✅ Staged {len(data)} row(s) — validating...")

        # Stage 2: Only now swap — truncate + copy in one transaction
        if truncate_first:
            cur.execute(f"TRUNCATE TABLE {table_name}")
        cur.execute(f"INSERT INTO {table_name} ({col_str}) SELECT {col_str} FROM {staging}")
        cur.execute(f"DROP TABLE IF EXISTS {staging}")

        conn.commit()
        print(f"  ✅ Loaded {len(data)} row(s) into {table_name}")

    except Exception as e:
        conn.rollback()
        print(f"  ❌ Failed to load {table_name}: {e}")
        print(f"  ℹ️  Original data preserved (staging failed before swap)")
        raise

    finally:
        cur.close()


# ==========================================================
#  MAIN LOADER
# ==========================================================
def load_matrix(
    load_type: str = "both",
    container_name: str = DEFAULT_CONTAINER,
    dry_run: bool = False,
):
    """
    Load carrier rules and/or column mapping CSVs from
    Azure Blob Storage into PostgreSQL.

    Parameters
    ----------
    load_type : str
        'rules', 'mapping', or 'both'.
    container_name : str
        Blob container name.
    dry_run : bool
        If True, validate only without writing to DB.
    """
    print(f"\n{'='*60}")
    print(f"  MATRIX LOADER — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Mode: {load_type.upper()} | Dry Run: {dry_run}")
    print(f"  Source: {container_name}/{BLOB_BASE_PATH}")
    print(f"{'='*60}\n")

    # Connect to blob storage
    blob_client = authenticate_blob_storage()

    # Connect to DB (unless dry run)
    conn = None
    if not dry_run:
        conn = get_postgres_connection()

    success = True

    # --- Carrier Rules ---
    if load_type in ("rules", "both"):
        print(f"📄 Carrier Rules: {RULES_BLOB_NAME}")
        try:
            rules_df = read_csv_from_blob(blob_client, RULES_BLOB_NAME, container_name)
            is_valid = validate_csv(rules_df, REQUIRED_RULES_COLUMNS, CARRIER_RULES_TABLE)

            if is_valid and not dry_run:
                load_csv_to_table(conn, rules_df, CARRIER_RULES_TABLE)
            elif not is_valid:
                success = False

        except FileNotFoundError as e:
            print(f"  ❌ {e}")
            success = False
        except Exception as e:
            print(f"  ❌ Error: {e}")
            success = False

    # --- Column Mapping ---
    if load_type in ("mapping", "both"):
        print(f"\n📄 Column Mapping: {MAPPING_BLOB_NAME}")
        try:
            mapping_df = read_csv_from_blob(blob_client, MAPPING_BLOB_NAME, container_name)
            is_valid = validate_csv(mapping_df, REQUIRED_MAPPING_COLUMNS, COLUMN_MAPPING_TABLE)

            if is_valid and not dry_run:
                load_csv_to_table(conn, mapping_df, COLUMN_MAPPING_TABLE)
            elif not is_valid:
                success = False

        except FileNotFoundError as e:
            print(f"  ❌ {e}")
            success = False
        except Exception as e:
            print(f"  ❌ Error: {e}")
            success = False

    # Cleanup
    if conn:
        conn.close()

    print(f"\n{'='*60}")
    if dry_run:
        print(f"  DRY RUN COMPLETE — no data written")
    elif success:
        print(f"  LOAD COMPLETE ✅")
    else:
        print(f"  LOAD COMPLETED WITH ERRORS ❌")
    print(f"{'='*60}\n")

    return success


# ==========================================================
#  CLI
# ==========================================================
def main():
    parser = argparse.ArgumentParser(
        description="Load carrier rules and/or column mapping CSVs from Azure Blob Storage into PostgreSQL."
    )
    parser.add_argument(
        "--load",
        choices=["rules", "mapping", "both"],
        default="both",
        help="What to load: 'rules', 'mapping', or 'both' (default).",
    )
    parser.add_argument(
        "--container",
        default=DEFAULT_CONTAINER,
        help=f"Blob container name (default: {DEFAULT_CONTAINER}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate CSVs without writing to the database.",
    )

    args = parser.parse_args()

    success = load_matrix(
        load_type=args.load,
        container_name=args.container,
        dry_run=args.dry_run,
    )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()