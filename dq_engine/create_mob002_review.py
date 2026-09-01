from pathlib import Path
from uuid import uuid4

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "database" / "cdp.duckdb"


def main():
    conn = duckdb.connect(str(DB_PATH))

    # Check whether an unresolved/open review already exists.
    existing = conn.execute("""
        SELECT review_id
        FROM cdp.review_queue
        WHERE issue_type = 'POSSIBLE_MATCH'
          AND source_system = 'MOBILE'
          AND source_customer_id = 'MOB002'
          AND status != 'RESOLVED'
        LIMIT 1
    """).fetchone()

    if existing:
        print(
            f"MOB002 review already exists: {existing[0]}"
        )
        conn.close()
        return

    conn.execute(
        """
        INSERT INTO cdp.review_queue (
            review_id,
            issue_type,
            golden_id,
            source_system,
            source_customer_id,
            attribute_name,
            severity,
            reason,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            str(uuid4()),
            "POSSIBLE_MATCH",
            "G000002",
            "MOBILE",
            "MOB002",
            None,
            "MEDIUM",
            (
                "MOB002 is a possible match to "
                "G000002 based on DOB, phone, and email"
            ),
            "OPEN",
        ],
    )

    print("MOB002 review created.")

    print(
        conn.sql("""
            SELECT
                review_id,
                issue_type,
                golden_id,
                source_system,
                source_customer_id,
                severity,
                status,
                reason
            FROM cdp.review_queue
            WHERE source_system = 'MOBILE'
              AND source_customer_id = 'MOB002'
        """)
    )

    conn.close()


if __name__ == "__main__":
    main()