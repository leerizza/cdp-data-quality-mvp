from pathlib import Path

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "database" / "cdp.duckdb"


def main() -> None:
    conn = duckdb.connect(str(DB_PATH), read_only=True)

    print("\n=== CDP COUNTS ===")

    result = conn.sql("""
        SELECT
            (SELECT COUNT(*) FROM main.customer) AS source_customer,
            (SELECT COUNT(*) FROM main.stg_customer) AS staging_customer,
            (SELECT COUNT(*) FROM main.eligible_customer) AS eligible_customer,
            (SELECT COUNT(*) FROM main.gold_customer) AS gold_customer,
            (SELECT COUNT(*)
             FROM dq.quarantine_customer
             WHERE run_id = (
                 SELECT run_id
                 FROM dq.dq_run
                 WHERE dataset = 'stg_customer'
                 ORDER BY started_at DESC
                 LIMIT 1
             )) AS quarantine_rows
    """)

    print(result)

    print("\n=== GOLD CUSTOMER SAMPLE ===")

    print(
        conn.sql("""
            SELECT *
            FROM main.gold_customer
            ORDER BY customer_id
            LIMIT 10
        """)
    )

    conn.close()


if __name__ == "__main__":
    main()