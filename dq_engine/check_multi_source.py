from pathlib import Path

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "database" / "cdp.duckdb"


def main() -> None:
    conn = duckdb.connect(str(DB_PATH), read_only=True)

    print("\n=== SOURCE COUNTS ===")

    print(
        conn.sql("""
            SELECT
                source_system,
                COUNT(*) AS records
            FROM main.customer_unified
            GROUP BY source_system
            ORDER BY source_system
        """)
    )

    print("\n=== UNIFIED DATA ===")

    print(
        conn.sql("""
            SELECT
                source_system,
                source_customer_id,
                nik,
                full_name,
                phone,
                email,
                birth_date
            FROM main.customer_unified
            ORDER BY source_system, source_customer_id
        """)
    )

    conn.close()


if __name__ == "__main__":
    main()