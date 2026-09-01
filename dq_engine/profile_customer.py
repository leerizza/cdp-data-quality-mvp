from pathlib import Path

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "database" / "cdp.duckdb"


def main() -> None:
    conn = duckdb.connect(str(DB_PATH), read_only=True)

    print("\n=== DATASET OVERVIEW ===")

    overview = conn.sql("""
        SELECT
            COUNT(*) AS total_rows,
            COUNT(*) - COUNT(customer_id) AS null_customer_id,
            COUNT(*) - COUNT(nik) AS null_nik,
            COUNT(*) - COUNT(full_name) AS null_full_name,
            COUNT(*) - COUNT(phone) AS null_phone,
            COUNT(*) - COUNT(email) AS null_email,
            COUNT(*) - COUNT(birth_date) AS null_birth_date,
            COUNT(DISTINCT customer_id) AS unique_customer_id,
            COUNT(DISTINCT nik) AS unique_nik
        FROM main.stg_customer
    """)

    print(overview)

    print("\n=== DUPLICATE CUSTOMER ID ===")

    duplicates = conn.sql("""
        SELECT
            customer_id,
            COUNT(*) AS cnt
        FROM main.stg_customer
        GROUP BY customer_id
        HAVING COUNT(*) > 1
        ORDER BY cnt DESC
    """)

    print(duplicates)

    print("\n=== DUPLICATE NIK ===")

    duplicate_nik = conn.sql("""
        SELECT
            nik,
            COUNT(*) AS cnt
        FROM main.stg_customer
        WHERE nik IS NOT NULL
        GROUP BY nik
        HAVING COUNT(*) > 1
        ORDER BY cnt DESC
    """)

    print(duplicate_nik)

    print("\n=== NIK LENGTH DISTRIBUTION ===")

    nik_length = conn.sql("""
        SELECT
            LENGTH(nik) AS nik_length,
            COUNT(*) AS cnt
        FROM main.stg_customer
        WHERE nik IS NOT NULL
        GROUP BY LENGTH(nik)
        ORDER BY nik_length
    """)

    print(nik_length)

    print("\n=== PHONE FORMAT ===")

    phone_format = conn.sql("""
        SELECT
            CASE
                WHEN phone IS NULL THEN 'NULL'
                WHEN regexp_matches(phone, '^08[0-9]{8,13}$')
                    THEN 'VALID'
                ELSE 'INVALID'
            END AS status,
            COUNT(*) AS cnt
        FROM main.stg_customer
        GROUP BY 1
        ORDER BY 1
    """)

    print(phone_format)

    print("\n=== EMAIL FORMAT ===")

    email_format = conn.sql("""
        SELECT
            CASE
                WHEN email IS NULL THEN 'NULL'
                WHEN regexp_matches(
                    lower(email),
                    '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$'
                )
                    THEN 'VALID'
                ELSE 'INVALID'
            END AS status,
            COUNT(*) AS cnt
        FROM main.stg_customer
        GROUP BY 1
        ORDER BY 1
    """)

    print(email_format)

    print("\n=== BIRTH DATE ===")

    birth_date = conn.sql("""
        SELECT
            COUNT(*) AS total_with_birth_date,
            COUNT(*) FILTER (
                WHERE birth_date > CURRENT_DATE
            ) AS future_birth_date,
            MIN(
                date_diff('year', birth_date, CURRENT_DATE)
            ) AS min_age,
            MAX(
                date_diff('year', birth_date, CURRENT_DATE)
            ) AS max_age
        FROM main.stg_customer
        WHERE birth_date IS NOT NULL
    """)

    print(birth_date)

    conn.close()


if __name__ == "__main__":
    main()