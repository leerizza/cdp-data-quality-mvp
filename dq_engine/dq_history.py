"""Per-run history snapshot and trend.

Source-layer tables are keyed by run_id, but the golden layer holds only
current state - cdp.golden_entity has no run_id, so a past run's entity
counts cannot be rebuilt after the fact. Each run therefore captures its
own snapshot here, and earlier rows are left alone.

Run this at the end of a pipeline, after the score and gate stages.
"""

from __future__ import annotations

from pathlib import Path

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "database" / "cdp.duckdb"


def scalar(conn, sql: str, params: list | None = None):
    row = conn.execute(sql, params or []).fetchone()
    return row[0] if row else None


def snapshot(conn, run_id: str) -> None:
    started_at = scalar(conn, "SELECT started_at FROM dq.dq_run WHERE run_id = ?", [run_id])
    total = scalar(conn, "SELECT total_records FROM dq.dq_run WHERE run_id = ?", [run_id]) or 0

    quarantined = scalar(
        conn,
        "SELECT COUNT(DISTINCT customer_id) FROM dq.quarantine_customer WHERE run_id = ?",
        [run_id],
    )

    pass_rate = ((total - quarantined) / total * 100.0) if total else None

    critical_fails = scalar(
        conn,
        """
        SELECT COUNT(*) FROM dq.dq_summary
        WHERE run_id = ? AND severity = 'CRITICAL' AND failed_records > 0
        """,
        [run_id],
    )

    high_fails = scalar(
        conn,
        """
        SELECT COUNT(*) FROM dq.dq_summary
        WHERE run_id = ? AND severity = 'HIGH' AND status = 'FAIL'
        """,
        [run_id],
    )

    entities = scalar(conn, "SELECT COUNT(*) FROM cdp.golden_entity")
    customers = scalar(conn, "SELECT COUNT(*) FROM cdp.golden_customer")
    assessed = scalar(
        conn,
        "SELECT COUNT(*) FROM cdp.golden_quality_score WHERE quality_score IS NOT NULL",
    )

    open_reviews = scalar(
        conn, "SELECT COUNT(*) FROM cdp.review_queue WHERE status = 'OPEN'"
    )
    high_open = scalar(
        conn,
        "SELECT COUNT(*) FROM cdp.review_queue WHERE status = 'OPEN' AND severity = 'HIGH'",
    )

    overall = scalar(
        conn, "SELECT overall_score FROM cdp.cdp_quality_score WHERE run_id = ?", [run_id]
    )
    gate = scalar(
        conn, "SELECT gate_status FROM cdp.cdp_quality_gate WHERE run_id = ?", [run_id]
    )

    conn.execute("DELETE FROM dq.dq_run_history WHERE run_id = ?", [run_id])
    conn.execute(
        """
        INSERT INTO dq.dq_run_history (
            run_id, started_at,
            total_records, quarantined_records, source_pass_rate,
            critical_failed_rules, high_failed_rules,
            golden_entities, golden_customers, assessed_entities,
            open_reviews, high_open_reviews,
            cdp_overall_score, cdp_gate_status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            run_id,
            started_at,
            total,
            quarantined,
            pass_rate,
            critical_fails,
            high_fails,
            entities,
            customers,
            assessed,
            open_reviews,
            high_open,
            overall,
            gate,
        ],
    )


def arrow(current, previous, higher_is_better=True) -> str:
    """Direction marker comparing this run with the one before it."""
    if current is None or previous is None:
        return "  -"
    delta = current - previous
    if abs(delta) < 0.05:
        return "  ="
    better = delta > 0 if higher_is_better else delta < 0
    sign = "+" if delta > 0 else ""
    return f"{'UP ' if better else 'DOWN'} {sign}{delta:.1f}"


def show_trend(conn) -> None:
    rows = conn.execute(
        """
        SELECT
            run_id, started_at, source_pass_rate, quarantined_records,
            critical_failed_rules, open_reviews, cdp_overall_score, cdp_gate_status
        FROM dq.dq_run_history
        ORDER BY started_at
        """
    ).fetchall()

    print("\n=== RUN HISTORY ===")
    print(
        conn.sql(
            """
            SELECT
                run_id,
                ROUND(source_pass_rate, 2) AS source_pass_rate,
                quarantined_records AS quarantined,
                critical_failed_rules AS critical,
                open_reviews AS reviews,
                ROUND(cdp_overall_score, 1) AS cdp_score,
                cdp_gate_status AS gate
            FROM dq.dq_run_history
            ORDER BY started_at
            """
        )
    )

    if len(rows) < 2:
        print("\n(only one run recorded - trend needs at least two)")
        return

    prev, curr = rows[-2], rows[-1]
    print("\n=== TREND vs PREVIOUS RUN ===")
    print(f"  previous : {prev[0]}")
    print(f"  current  : {curr[0]}")
    print(f"  source pass rate : {arrow(curr[2], prev[2])}")
    print(f"  quarantined      : {arrow(curr[3], prev[3], higher_is_better=False)}")
    print(f"  critical breaches: {arrow(curr[4], prev[4], higher_is_better=False)}")
    print(f"  open reviews     : {arrow(curr[5], prev[5], higher_is_better=False)}")
    print(f"  CDP score        : {arrow(curr[6], prev[6])}")

    if prev[7] != curr[7]:
        print(f"  gate status      : {prev[7]} -> {curr[7]}")


def main() -> int:
    conn = duckdb.connect(str(DB_PATH))

    run_id = scalar(conn, "SELECT run_id FROM dq.dq_run ORDER BY started_at DESC LIMIT 1")
    if run_id is None:
        raise RuntimeError("No DQ run found - run the dq stage first.")

    snapshot(conn, run_id)
    show_trend(conn)

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
