"""Identity resolution metrics.

The CDP score used to measure identity as resolved/total source records.
That reads 12/15 = 80% here, but three of those twelve are members of an
entity flagged REVIEW because its sources disagree - counting them as
fully resolved overstates how settled identity actually is.

This computes the picture properly:

  pair level    (denominator: every evaluated pair)
    match_rate, possible_match_rate, conflict_rate

  record level  (denominator: every source record)
    unresolved_rate, plus the confirmed / review / unresolved split

  cluster shape
    golden_entity_count, avg_cluster_size

  resolution_health
    the single number the CDP score consumes. Confirmed records count
    in full, records awaiting review count partially, and the pair
    conflict rate is deducted. Both weights live in
    metadata/quality_threshold.csv rather than in this file.
"""

from __future__ import annotations

from pathlib import Path

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "database" / "cdp.duckdb"
THRESHOLD_CSV = PROJECT_ROOT / "metadata" / "quality_threshold.csv"


def load_thresholds(conn, scope: str) -> dict[str, float]:
    """Read the ACTIVE thresholds for one scope from metadata."""
    if not THRESHOLD_CSV.exists():
        raise RuntimeError(f"Thresholds not found: {THRESHOLD_CSV}")

    rows = conn.execute(
        """
        SELECT key, CAST(value AS DOUBLE)
        FROM read_csv_auto(?)
        WHERE scope = ? AND upper(status) = 'ACTIVE'
        """,
        [str(THRESHOLD_CSV), scope],
    ).fetchall()

    if not rows:
        raise RuntimeError(
            f"No ACTIVE thresholds for scope '{scope}' in {THRESHOLD_CSV.name}"
        )

    return {key: value for key, value in rows}


def rate(part: int, whole: int) -> float | None:
    """Percentage, or None when there is nothing to divide by."""
    if not whole:
        return None
    return part / whole * 100.0


def compute(conn, thresholds: dict[str, float]) -> dict:
    """Gather every metric from the current identity tables."""
    pair_counts = conn.execute(
        """
        SELECT
            COUNT(*),
            COUNT(*) FILTER (WHERE match_status = 'MATCH'),
            COUNT(*) FILTER (WHERE match_status = 'POSSIBLE_MATCH'),
            COUNT(*) FILTER (WHERE match_status IN ('CONFLICT', 'MATCH_WITH_CONFLICT'))
        FROM cdp.identity_match
        """
    ).fetchone()

    total_pairs, matches, possibles, conflicts = pair_counts

    record_counts = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM main.customer_unified),
            COUNT(*) FILTER (WHERE m.membership_status = 'CONFIRMED'),
            COUNT(*) FILTER (WHERE m.membership_status = 'REVIEW')
        FROM main.customer_unified u
        LEFT JOIN cdp.golden_entity_member m
               ON m.source_system = u.source_system
              AND m.source_customer_id = u.source_customer_id
        """
    ).fetchone()

    total_records, confirmed, in_review = record_counts
    unresolved = total_records - confirmed - in_review

    cluster_shape = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM cdp.golden_entity),
            (SELECT COUNT(*) FROM cdp.golden_entity_member)
        """
    ).fetchone()

    entity_count, member_count = cluster_shape
    avg_cluster_size = (member_count / entity_count) if entity_count else None

    conflict_rate = rate(conflicts, total_pairs)

    # Confirmed counts in full, REVIEW partially, unresolved not at all;
    # then the pair conflict rate is deducted, so unresolved disagreement
    # between sources shows up even when everything landed in an entity.
    if total_records:
        weighted = confirmed + in_review * thresholds["review_member_weight"]
        health = weighted / total_records * 100.0
        health -= (conflict_rate or 0.0) * thresholds["conflict_penalty_weight"]
        health = max(0.0, min(100.0, health))
    else:
        health = None

    return {
        "total_pairs": total_pairs,
        "match_rate": rate(matches, total_pairs),
        "possible_match_rate": rate(possibles, total_pairs),
        "conflict_rate": conflict_rate,
        "total_source_records": total_records,
        "confirmed_records": confirmed,
        "review_records": in_review,
        "unresolved_records": unresolved,
        "unresolved_rate": rate(unresolved, total_records),
        "golden_entity_count": entity_count,
        "avg_cluster_size": avg_cluster_size,
        "resolution_health": health,
    }


def main() -> int:
    conn = duckdb.connect(str(DB_PATH))

    thresholds = load_thresholds(conn, "identity_metrics")

    run = conn.execute(
        "SELECT run_id FROM dq.dq_run ORDER BY started_at DESC LIMIT 1"
    ).fetchone()

    if run is None:
        raise RuntimeError("No DQ run found - run the dq stage first.")

    run_id = run[0]
    metrics = compute(conn, thresholds)

    conn.execute("DELETE FROM cdp.identity_metrics WHERE run_id = ?", [run_id])
    conn.execute(
        """
        INSERT INTO cdp.identity_metrics (
            run_id, total_pairs, match_rate, possible_match_rate, conflict_rate,
            total_source_records, confirmed_records, review_records,
            unresolved_records, unresolved_rate,
            golden_entity_count, avg_cluster_size, resolution_health
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            run_id,
            metrics["total_pairs"],
            metrics["match_rate"],
            metrics["possible_match_rate"],
            metrics["conflict_rate"],
            metrics["total_source_records"],
            metrics["confirmed_records"],
            metrics["review_records"],
            metrics["unresolved_records"],
            metrics["unresolved_rate"],
            metrics["golden_entity_count"],
            metrics["avg_cluster_size"],
            metrics["resolution_health"],
        ],
    )

    print("\n=== IDENTITY METRICS ===")
    print(f"Run ID : {run_id}")
    print(
        conn.sql(
            """
            SELECT
                total_pairs,
                ROUND(match_rate, 1) AS match_rate,
                ROUND(possible_match_rate, 1) AS possible_rate,
                ROUND(conflict_rate, 1) AS conflict_rate
            FROM cdp.identity_metrics WHERE run_id = ?
            """,
            params=[run_id],
        )
    )
    print(
        conn.sql(
            """
            SELECT
                total_source_records AS records,
                confirmed_records AS confirmed,
                review_records AS in_review,
                unresolved_records AS unresolved,
                ROUND(unresolved_rate, 1) AS unresolved_rate,
                golden_entity_count AS entities,
                ROUND(avg_cluster_size, 2) AS avg_cluster,
                ROUND(resolution_health, 1) AS health
            FROM cdp.identity_metrics WHERE run_id = ?
            """,
            params=[run_id],
        )
    )

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
