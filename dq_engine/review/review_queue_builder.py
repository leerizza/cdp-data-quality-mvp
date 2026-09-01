from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "database" / "cdp.duckdb"


def review_id(*parts) -> str:
    """Stable id for one reviewable issue.

    Derived from the issue's identity rather than random, so rebuilding
    the queue re-uses the same review_id and a reviewer's decision on it
    survives the rebuild.
    """
    key = "|".join("" if p is None else str(p) for p in parts)
    return str(uuid5(NAMESPACE_URL, key))


def main():
    conn = duckdb.connect(str(DB_PATH))

    # Rebuild only the untouched part of the queue; anything a reviewer
    # has already acted on is kept.
    conn.execute("""
        DELETE FROM cdp.review_queue
        WHERE status = 'OPEN'
    """)

    # =========================================================
    # 1. Identity conflicts
    # =========================================================

    conflicts = conn.execute("""
    SELECT DISTINCT
        im.source_system,
        im.source_customer_id,
        im.candidate_source_system,
        im.candidate_source_customer_id,
        ge.golden_id,
        im.match_status,
        im.reason
    FROM cdp.identity_match im

    LEFT JOIN cdp.golden_entity_member gem
        ON im.source_system = gem.source_system
    AND im.source_customer_id = gem.source_customer_id

    LEFT JOIN cdp.golden_entity ge
        ON gem.golden_id = ge.golden_id

    WHERE im.match_status IN (
        'CONFLICT',
        'MATCH_WITH_CONFLICT'
    )
    """).fetchall()

    for (
        source_system,
        source_customer_id,
        candidate_source_system,
        candidate_source_customer_id,
        golden_id,
        match_status,
        reason,
    ) in conflicts:

        conflict_reason = (
            f"{match_status} against "
            f"{candidate_source_system}:{candidate_source_customer_id}"
            f" - {reason}"
        )

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
            ON CONFLICT (review_id) DO NOTHING
            """,
            [
                review_id(
                    "IDENTITY_CONFLICT",
                    golden_id,
                    source_system,
                    source_customer_id,
                    candidate_source_customer_id,
                ),
                "IDENTITY_CONFLICT",
                golden_id,
                source_system,
                source_customer_id,
                None,
                "HIGH",
                conflict_reason,
                "OPEN",
            ],
        )

    # =========================================================
    # 2. Cross-source conflicts
    # =========================================================

    consistency_conflicts = conn.execute("""
        SELECT
            golden_id,
            attribute_name,
            COALESCE(severity, 'MEDIUM') AS severity,
            COALESCE(source_values, 'conflicting values across sources')
                AS source_values
        FROM cdp.cross_source_consistency
        WHERE consistency_status = 'CONFLICT'
    """).fetchall()

    for (
        golden_id,
        attribute_name,
        severity,
        source_values,
    ) in consistency_conflicts:

        conn.execute(
            """
            INSERT INTO cdp.review_queue (
                review_id,
                issue_type,
                golden_id,
                attribute_name,
                severity,
                reason,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (review_id) DO NOTHING
            """,
            [
                review_id(
                    "ATTRIBUTE_CONFLICT",
                    golden_id,
                    attribute_name,
                ),
                "ATTRIBUTE_CONFLICT",
                golden_id,
                attribute_name,
                severity,
                source_values,
                "OPEN",
            ],
        )

    # =========================================================
    # 3. Possible identity matches
    # =========================================================

    # A pair whose two sides already sit in the same golden entity has
    # been settled by clustering (usually off the back of an approved
    # resolution), so it is no longer something a steward can act on.
    possible = conn.execute("""
        SELECT DISTINCT
            im.source_system,
            im.source_customer_id,
            im.candidate_source_system,
            im.candidate_source_customer_id,
            im.reason
        FROM cdp.identity_match im
        WHERE im.match_status = 'POSSIBLE_MATCH'
          AND NOT EXISTS (
              SELECT 1
              FROM cdp.golden_entity_member m1
              JOIN cdp.golden_entity_member m2
                ON m1.golden_id = m2.golden_id
              WHERE m1.source_system = im.source_system
                AND m1.source_customer_id = im.source_customer_id
                AND m2.source_system = im.candidate_source_system
                AND m2.source_customer_id = im.candidate_source_customer_id
          )
    """).fetchall()

    for (
        source_system,
        source_customer_id,
        candidate_source_system,
        candidate_source_customer_id,
        reason,
    ) in possible:

        possible_reason = (
            f"POSSIBLE_MATCH against "
            f"{candidate_source_system}:{candidate_source_customer_id}"
            f" - {reason}"
        )

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
            ON CONFLICT (review_id) DO NOTHING
            """,
            [
                review_id(
                    "POSSIBLE_MATCH",
                    source_system,
                    source_customer_id,
                    candidate_source_customer_id,
                ),
                "POSSIBLE_MATCH",
                None,
                source_system,
                source_customer_id,
                None,
                "MEDIUM",
                possible_reason,
                "OPEN",
            ],
        )

    print("\n=== REVIEW QUEUE ===")

    print(
        conn.sql("""
            SELECT
                review_id,
                issue_type,
                golden_id,
                source_system,
                source_customer_id,
                attribute_name,
                severity,
                status,
                reason
            FROM cdp.review_queue
            ORDER BY
                severity DESC,
                issue_type,
                source_system,
                source_customer_id
        """)
    )

    print("\n=== REVIEW SUMMARY ===")

    print(
        conn.sql("""
            SELECT
                issue_type,
                severity,
                status,
                COUNT(*) AS total
            FROM cdp.review_queue
            GROUP BY
                issue_type,
                severity,
                status
            ORDER BY
                issue_type,
                severity
        """)
    )

    conn.close()


if __name__ == "__main__":
    main()