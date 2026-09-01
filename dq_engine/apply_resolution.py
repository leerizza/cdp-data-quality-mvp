from pathlib import Path
from uuid import uuid4

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "database" / "cdp.duckdb"


TARGET_SOURCE_SYSTEM = "MOBILE"
TARGET_SOURCE_CUSTOMER_ID = "MOB002"
TARGET_GOLDEN_ID = "G000002"


def main():
    conn = duckdb.connect(str(DB_PATH))

    # ============================================================
    # Find the exact review we intend to resolve.
    # ============================================================

    review = conn.execute(
        """
        SELECT
            review_id,
            source_system,
            source_customer_id
        FROM cdp.review_queue
        WHERE issue_type = 'POSSIBLE_MATCH'
          AND status = 'OPEN'
          AND source_system = ?
          AND source_customer_id = ?
        LIMIT 1
        """,
        [
            TARGET_SOURCE_SYSTEM,
            TARGET_SOURCE_CUSTOMER_ID,
        ],
    ).fetchone()

    if review is None:
        print(
            f"No OPEN POSSIBLE_MATCH review found for "
            f"{TARGET_SOURCE_SYSTEM}/{TARGET_SOURCE_CUSTOMER_ID}"
        )
        conn.close()
        return

    review_id, source_system, source_customer_id = review

    # ============================================================
    # Validate Golden Entity.
    # ============================================================

    golden_exists = conn.execute(
        """
        SELECT 1
        FROM cdp.golden_entity
        WHERE golden_id = ?
          AND entity_status = 'ACTIVE'
        """,
        [TARGET_GOLDEN_ID],
    ).fetchone()

    if golden_exists is None:
        raise RuntimeError(
            f"{TARGET_GOLDEN_ID} is not an ACTIVE Golden Entity."
        )

    # ============================================================
    # Check current membership.
    # ============================================================

    current_membership = conn.execute(
        """
        SELECT
            golden_id,
            membership_status
        FROM cdp.golden_entity_member
        WHERE source_system = ?
          AND source_customer_id = ?
        """,
        [
            source_system,
            source_customer_id,
        ],
    ).fetchone()

    if current_membership:
        print(
            "Current membership:",
            current_membership
        )

    # ============================================================
    # Remove old membership if any.
    # ============================================================

    conn.execute(
        """
        DELETE FROM cdp.golden_entity_member
        WHERE source_system = ?
          AND source_customer_id = ?
        """,
        [
            source_system,
            source_customer_id,
        ],
    )

    # ============================================================
    # Confirm membership.
    # ============================================================

    conn.execute(
        """
        INSERT INTO cdp.golden_entity_member (
            golden_id,
            source_system,
            source_customer_id,
            membership_status,
            membership_confidence
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            TARGET_GOLDEN_ID,
            source_system,
            source_customer_id,
            "CONFIRMED",
            "HIGH",
        ],
    )

    # ============================================================
    # Audit action.
    # ============================================================

    conn.execute(
        """
        INSERT INTO cdp.identity_resolution_action (
            action_id,
            review_id,
            action_type,
            source_system,
            source_customer_id,
            golden_id,
            performed_by,
            reason
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            str(uuid4()),
            review_id,
            "APPROVED",
            source_system,
            source_customer_id,
            TARGET_GOLDEN_ID,
            "DATA_STEWARD",
            (
                "Data Steward approved MOB002 as a member "
                "of G000002 based on matching phone, email, "
                "and birth date."
            ),
        ],
    )

    # ============================================================
    # Close review.
    # ============================================================

    conn.execute(
        """
        UPDATE cdp.review_queue
        SET
            status = 'RESOLVED',
            resolution = 'APPROVED',
            assigned_to = 'DATA_STEWARD',
            resolved_at = CURRENT_TIMESTAMP
        WHERE review_id = ?
        """,
        [review_id],
    )

    print("\n=== RESOLUTION APPLIED ===")
    print(f"Review ID       : {review_id}")
    print(f"Source          : {source_system}/{source_customer_id}")
    print(f"Golden ID       : {TARGET_GOLDEN_ID}")
    print("Action          : APPROVED")
    print("Status          : RESOLVED")

    print("\n=== GOLDEN MEMBERSHIP ===")

    print(
        conn.sql(
            """
            SELECT
                golden_id,
                source_system,
                source_customer_id,
                membership_status,
                membership_confidence
            FROM cdp.golden_entity_member
            WHERE golden_id = ?
            ORDER BY source_system, source_customer_id
            """,
            params=[TARGET_GOLDEN_ID],
        )
    )

    print("\n=== RESOLUTION AUDIT ===")

    print(
        conn.sql(
            """
            SELECT
                review_id,
                action_type,
                source_system,
                source_customer_id,
                golden_id,
                performed_by,
                reason,
                created_at
            FROM cdp.identity_resolution_action
            WHERE review_id = ?
            ORDER BY created_at
            """,
            params=[review_id],
        )
    )

    conn.close()


if __name__ == "__main__":
    main()