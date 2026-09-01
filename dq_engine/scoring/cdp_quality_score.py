"""Overall CDP Quality Score.

Rolls the per-layer signals the pipeline already produces into one
explainable number for the CDP as a whole. Every dimension is stored
with its own score, weight and a plain-language detail string, so the
overall figure can always be taken apart again.

Dimensions:
    source_dq       records that survived source DQ (not quarantined)
    identity        source records resolved into a golden entity
    golden_quality  average quality of entities that were actually scored
    consistency     attribute groups agreeing across sources
    review_health   review backlog, weighted towards HIGH severity

Entities still awaiting review are NOT scored as zero: they are excluded
from golden_quality and reported as coverage instead. Scoring "not yet
assessed" as 0 would understate quality rather than describe it.

Keyed by the latest dq_run so scores line up with the run that produced
them, and rows accumulate for trend analysis.
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


# Populated from metadata at run time; see main().
WEIGHTS: dict[str, float] = {}
STATUS_BANDS: list[tuple[float, str]] = []


def pct(numerator: float, denominator: float) -> float | None:
    if not denominator:
        return None
    return (numerator / denominator) * 100.0


def score_source_dq(conn, run_id):
    total = conn.execute(
        "SELECT total_records FROM dq.dq_run WHERE run_id = ?", [run_id]
    ).fetchone()[0]

    quarantined = conn.execute(
        "SELECT COUNT(DISTINCT customer_id) FROM dq.quarantine_customer WHERE run_id = ?",
        [run_id],
    ).fetchone()[0]

    clean = (total or 0) - quarantined
    return pct(clean, total), f"{clean}/{total} records passed source DQ"


def score_identity(conn, run_id):
    """Read resolution health from cdp.identity_metrics.

    This used to be resolved/total source records, which counted a
    record sitting in a REVIEW entity as fully resolved. identity_metrics
    weights those partially and deducts the pair conflict rate; see
    dq_engine/identity/identity_metrics.py.
    """
    row = conn.execute(
        """
        SELECT resolution_health, confirmed_records, review_records,
               unresolved_records, total_source_records
        FROM cdp.identity_metrics
        WHERE run_id = ?
        """,
        [run_id],
    ).fetchone()

    if row is None:
        return None, "identity metrics not computed for this run"

    health, confirmed, in_review, unresolved, total = row

    return health, (
        f"{confirmed}/{total} confirmed, {in_review} in review, "
        f"{unresolved} unresolved"
    )


def score_golden_quality(conn):
    row = conn.execute(
        """
        SELECT
            COUNT(*) FILTER (WHERE quality_score IS NOT NULL),
            COUNT(*),
            AVG(quality_score) FILTER (WHERE quality_score IS NOT NULL)
        FROM cdp.golden_quality_score
        """
    ).fetchone()

    assessed, total, avg_score = row[0], row[1], row[2]
    detail = f"{assessed}/{total} entities assessed, avg score {avg_score:.1f}" if assessed else "no entities assessed"
    return avg_score, detail, assessed, total


def score_consistency(conn):
    row = conn.execute(
        """
        SELECT
            COUNT(*) FILTER (WHERE consistency_status <> 'CONFLICT'),
            COUNT(*)
        FROM cdp.cross_source_consistency
        """
    ).fetchone()
    agreeing, total = row
    return pct(agreeing, total), f"{agreeing}/{total} attribute groups without conflict"


def score_review_health(conn, penalty_high: float, penalty_other: float):
    """100 when nothing is open; HIGH-severity items cost more."""
    row = conn.execute(
        """
        SELECT
            COUNT(*) FILTER (WHERE severity = 'HIGH'),
            COUNT(*)
        FROM cdp.review_queue
        WHERE status = 'OPEN'
        """
    ).fetchone()
    high, total = row

    penalty = min(100.0, high * penalty_high + (total - high) * penalty_other)
    detail = f"{total} open review(s), {high} HIGH" if total else "no open reviews"
    return 100.0 - penalty, detail


def band(score: float | None) -> str:
    if score is None:
        return "NOT_ASSESSED"
    for threshold, label in STATUS_BANDS:
        if score >= threshold:
            return label
    return "POOR"


def main() -> int:
    conn = duckdb.connect(str(DB_PATH))

    thresholds = load_thresholds(conn, "cdp_score")
    WEIGHTS.update({
        "source_dq": thresholds["weight_source_dq"],
        "identity": thresholds["weight_identity"],
        "golden_quality": thresholds["weight_golden_quality"],
        "consistency": thresholds["weight_consistency"],
        "review_health": thresholds["weight_review_health"],
    })
    STATUS_BANDS[:] = [
        (thresholds["band_excellent"], "EXCELLENT"),
        (thresholds["band_good"], "GOOD"),
        (thresholds["band_warning"], "WARNING"),
        (0.0, "POOR"),
    ]
    print(f"Loaded CDP score weights from {THRESHOLD_CSV.name}")

    run = conn.execute(
        "SELECT run_id FROM dq.dq_run ORDER BY started_at DESC LIMIT 1"
    ).fetchone()

    if run is None:
        raise RuntimeError("No DQ run found - run the dq stage first.")

    run_id = run[0]

    src_score, src_detail = score_source_dq(conn, run_id)
    idn_score, idn_detail = score_identity(conn, run_id)
    gld_score, gld_detail, assessed, total_entities = score_golden_quality(conn)
    cns_score, cns_detail = score_consistency(conn)
    rvw_score, rvw_detail = score_review_health(
        conn,
        thresholds["review_penalty_high"],
        thresholds["review_penalty_other"],
    )

    dimensions = [
        ("source_dq", src_score, src_detail),
        ("identity", idn_score, idn_detail),
        ("golden_quality", gld_score, gld_detail),
        ("consistency", cns_score, cns_detail),
        ("review_health", rvw_score, rvw_detail),
    ]

    # Re-weight across the dimensions that could actually be measured,
    # so a missing signal does not silently drag the total down.
    measurable = [(n, s, d) for n, s, d in dimensions if s is not None]
    weight_total = sum(WEIGHTS[n] for n, _, _ in measurable)

    overall = (
        sum(s * WEIGHTS[n] for n, s, _ in measurable) / weight_total
        if weight_total
        else None
    )

    conn.execute("DELETE FROM cdp.cdp_quality_dimension WHERE run_id = ?", [run_id])
    conn.execute("DELETE FROM cdp.cdp_quality_score WHERE run_id = ?", [run_id])

    for name, score, detail in dimensions:
        weight = WEIGHTS[name]
        contribution = (
            (score * weight / weight_total) if (score is not None and weight_total) else None
        )
        conn.execute(
            """
            INSERT INTO cdp.cdp_quality_dimension (
                run_id, dimension, score, weight, contribution, detail
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [run_id, name, score, weight, contribution, detail],
        )

    conn.execute(
        """
        INSERT INTO cdp.cdp_quality_score (
            run_id, overall_score, overall_status, assessed_entities, total_entities
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        [run_id, overall, band(overall), assessed, total_entities],
    )

    print("\n=== CDP QUALITY SCORE ===")
    print(f"Run ID : {run_id}")
    print(f"Overall: {overall:.1f} ({band(overall)})" if overall is not None else "Overall: n/a")
    print(f"Entity coverage: {assessed}/{total_entities} assessed")

    print("\n=== DIMENSIONS ===")
    print(
        conn.sql(
            """
            SELECT
                dimension,
                ROUND(score, 1) AS score,
                weight,
                ROUND(contribution, 1) AS contribution,
                detail
            FROM cdp.cdp_quality_dimension
            WHERE run_id = ?
            ORDER BY weight DESC, dimension
            """,
            params=[run_id],
        )
    )

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
