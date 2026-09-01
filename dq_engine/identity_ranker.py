from pathlib import Path
from uuid import uuid4

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "database" / "cdp.duckdb"


def main() -> None:
    conn = duckdb.connect(str(DB_PATH))

    conn.execute("""
        DELETE FROM cdp.identity_resolution_decision
    """)

    source_records = conn.execute("""
        SELECT DISTINCT
            source_system,
            source_customer_id
        FROM cdp.identity_match
        ORDER BY source_system, source_customer_id
    """).fetchall()

    for source_system, source_customer_id in source_records:

        candidates = conn.execute(
            """
            SELECT
                candidate_source_system,
                candidate_source_customer_id,
                match_score,
                match_status,
                matched_on,
                reason
            FROM cdp.identity_match
            WHERE source_system = ?
              AND source_customer_id = ?
            ORDER BY match_score DESC
            """,
            [
                source_system,
                source_customer_id,
            ],
        ).fetchall()

        if not candidates:
            continue

        best = candidates[0]

        (
            best_candidate_source_system,
            best_candidate_source_customer_id,
            best_score,
            best_status,
            best_matched_on,
            best_reason,
        ) = best

        second_score = (
            candidates[1][2]
            if len(candidates) > 1
            else None
        )

        score_gap = (
            best_score - second_score
            if second_score is not None
            else best_score
        )

        conflict_count = sum(
            1
            for candidate in candidates
            if candidate[3] == "CONFLICT"
        )

        has_conflict = conflict_count > 0

        # ---------------------------------------------------------
        # Confidence
        # ---------------------------------------------------------

        if (
            best_score >= 150
            and score_gap >= 30
            and not has_conflict
        ):
            confidence = "HIGH"

        elif (
            best_score >= 100
            and score_gap >= 20
        ):
            confidence = "MEDIUM"

        else:
            confidence = "LOW"

        # ---------------------------------------------------------
        # Final status
        # ---------------------------------------------------------

        if has_conflict and best_score >= 100:
            final_status = "MATCH_WITH_CONFLICT"

        elif best_status == "MATCH" and confidence == "HIGH":
            final_status = "MATCH"

        elif best_score >= 60:
            final_status = "POSSIBLE_MATCH"

        else:
            final_status = "NO_MATCH"

        decision_id = str(uuid4())

        reason = (
            f"best_score={best_score}; "
            f"score_gap={score_gap}; "
            f"conflict_count={conflict_count}"
        )

        conn.execute(
            """
            INSERT INTO cdp.identity_resolution_decision (
                decision_id,
                source_system,
                source_customer_id,
                best_candidate_source_system,
                best_candidate_source_customer_id,
                match_score,
                match_status,
                confidence,
                has_conflict,
                conflict_count,
                matched_on,
                reason
            )
            VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?
            )
            """,
            [
                decision_id,
                source_system,
                source_customer_id,

                best_candidate_source_system,
                best_candidate_source_customer_id,

                best_score,
                final_status,
                confidence,

                has_conflict,
                conflict_count,

                best_matched_on,
                reason,
            ],
        )

    print("\n=== IDENTITY RESOLUTION DECISIONS ===")

    print(
        conn.sql("""
            SELECT
                source_system,
                source_customer_id,
                best_candidate_source_system,
                best_candidate_source_customer_id,
                ROUND(match_score, 2) AS score,
                match_status,
                confidence,
                has_conflict,
                conflict_count,
                matched_on,
                reason
            FROM cdp.identity_resolution_decision
            ORDER BY
                source_system,
                source_customer_id
        """)
    )

    conn.close()


if __name__ == "__main__":
    main()