from pathlib import Path
from datetime import datetime

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "database" / "cdp.duckdb"


def resolve_review(
    conn,
    review_id,
    resolution,
    assigned_to="DATA_STEWARD",
):
    row = conn.execute(
        """
        SELECT
            review_id,
            issue_type,
            golden_id,
            source_system,
            source_customer_id,
            attribute_name,
            status
        FROM cdp.review_queue
        WHERE review_id = ?
        """,
        [review_id],
    ).fetchone()

    if row is None:
        raise ValueError(
            f"Review {review_id} not found."
        )

    if row[6] == "RESOLVED":
        raise ValueError(
            f"Review {review_id} already resolved."
        )

    conn.execute(
        """
        UPDATE cdp.review_queue
        SET
            status = 'RESOLVED',
            assigned_to = ?,
            resolution = ?,
            resolved_at = ?
        WHERE review_id = ?
        """,
        [
            assigned_to,
            resolution,
            datetime.now(),
            review_id,
        ],
    )


def main():

    conn = duckdb.connect(str(DB_PATH))

    print("\n=== OPEN REVIEWS ===")

    open_reviews = conn.sql(
        """
        SELECT
            review_id,
            issue_type,
            golden_id,
            source_system,
            source_customer_id,
            attribute_name,
            severity,
            status
        FROM cdp.review_queue
        WHERE status = 'OPEN'
        ORDER BY severity, issue_type
        """
    )

    print(open_reviews)

    # ----------------------------------------------------------
    # Demo:
    #
    # Resolve POSSIBLE_MATCH automatically as APPROVED
    # only for PoC demonstration.
    #
    # In real production this would require human approval.
    # ----------------------------------------------------------

    possible = conn.execute(
        """
        SELECT
            review_id
        FROM cdp.review_queue
        WHERE issue_type = 'POSSIBLE_MATCH'
          AND status = 'OPEN'
        ORDER BY created_at
        LIMIT 1
        """
    ).fetchone()

    if possible:

        review_id = possible[0]

        resolve_review(
            conn,
            review_id,
            "APPROVED",
        )

        print(
            f"\nResolved review: {review_id}"
        )

    print("\n=== REVIEW QUEUE AFTER RESOLUTION ===")

    print(
        conn.sql(
            """
            SELECT
                review_id,
                issue_type,
                golden_id,
                source_system,
                source_customer_id,
                attribute_name,
                severity,
                status,
                resolution
            FROM cdp.review_queue
            ORDER BY
                status,
                severity
            """
        )
    )

    conn.close()


if __name__ == "__main__":
    main()