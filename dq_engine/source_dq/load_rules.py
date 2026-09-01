from pathlib import Path

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[2]

DB_PATH = PROJECT_ROOT / "database" / "cdp.duckdb"
RULE_FILE = PROJECT_ROOT / "metadata" / "dq_rule_master.csv"


def main() -> None:
    print(f"Database : {DB_PATH}")
    print(f"Rule file: {RULE_FILE}")

    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"Database not found: {DB_PATH}"
        )

    if not RULE_FILE.exists():
        raise FileNotFoundError(
            f"Rule file not found: {RULE_FILE}"
        )

    conn = duckdb.connect(str(DB_PATH))

    # Make sure the column exists.
    conn.execute("""
        ALTER TABLE dq.dq_rule_master
        ADD COLUMN IF NOT EXISTS test_sql VARCHAR
    """)

    # Clear current registry.
    conn.execute("""
        DELETE FROM dq.dq_rule_master
    """)

    # Load metadata from CSV.
    conn.execute("""
        INSERT INTO dq.dq_rule_master
        (
            rule_id,
            domain,
            dataset,
            column_name,
            dimension,
            rule_name,
            severity,
            metric_type,
            threshold,
            implementation,
            status,
            owner,
            test_sql,
            is_active,
            created_at
        )
        SELECT
            rule_id,
            domain,
            dataset,
            column_name,
            dimension,
            rule_name,
            severity,
            metric_type,
            CAST(threshold AS DOUBLE),
            implementation,
            status,
            owner,
            test_sql,
            TRUE,
            CURRENT_TIMESTAMP
        FROM read_csv_auto(?)
    """, [str(RULE_FILE)])

    result = conn.sql("""
        SELECT
            rule_id,
            dataset,
            severity,
            threshold,
            implementation,
            status,
            test_sql
        FROM dq.dq_rule_master
        ORDER BY rule_id
    """)

    print("\n=== LOADED RULES ===")
    print(result)

    null_test_sql = conn.execute("""
        SELECT COUNT(*)
        FROM dq.dq_rule_master
        WHERE is_active = TRUE
          AND (
              test_sql IS NULL
              OR TRIM(test_sql) = ''
          )
    """).fetchone()[0]

    if null_test_sql > 0:
        raise ValueError(
            f"{null_test_sql} active rules have empty test_sql"
        )

    conn.close()

    print("\nRule registry loaded successfully.")


if __name__ == "__main__":
    main()