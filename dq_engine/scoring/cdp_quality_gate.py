"""CDP Quality Gate - is the golden layer fit to publish?

The source-level gate (dq_quality_gate.py) asks whether raw records are
clean enough to load. This one asks the CDP question: given identity
resolution, survivorship and the open review backlog, should the golden
customer layer be released downstream?

Hard rules win over the score - a failing CRITICAL source rule blocks
regardless of how good the average looks.

    BLOCKED   a hard rule tripped, or the overall score is below 70
    WARNING   something needs attention but the layer is usable
    PASSED    safe to publish

The verdict is written to cdp.cdp_quality_gate so the decision is
auditable after the fact, and exits non-zero when BLOCKED so a caller
can stop the pipeline.
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


def main() -> int:
    conn = duckdb.connect(str(DB_PATH))

    gate_thresholds = load_thresholds(conn, "cdp_gate")
    BLOCK_SCORE = gate_thresholds["block_below"]
    WARN_SCORE = gate_thresholds["warn_below"]

    score_row = conn.execute(
        """
        SELECT run_id, overall_score, overall_status, assessed_entities, total_entities
        FROM cdp.cdp_quality_score
        ORDER BY scored_at DESC
        LIMIT 1
        """
    ).fetchone()

    if score_row is None:
        raise RuntimeError("No CDP quality score found - run cdp_quality_score.py first.")

    run_id, overall, status, assessed, total_entities = score_row

    blocking: list[str] = []
    warning: list[str] = []

    # --- Hard rule: CRITICAL is zero tolerance.
    #
    # A single affected record blocks, whatever the rule's threshold
    # says. Thresholds express how much drift a rule tolerates, but a
    # CRITICAL breach (a null NIK, a duplicate customer id) is never
    # something to average away - so the record count decides here, not
    # the PASS/FAIL verdict.
    critical_fails = conn.execute(
        """
        SELECT rule_id, failed_records, status
        FROM dq.dq_summary
        WHERE run_id = ?
          AND severity = 'CRITICAL'
          AND failed_records > 0
        ORDER BY rule_id
        """,
        [run_id],
    ).fetchall()

    if critical_fails:
        detail = ", ".join(f"{r[0]} ({r[1]} record(s))" for r in critical_fails)
        blocking.append(f"CRITICAL rule breach - zero tolerance: {detail}")

    # --- Hard rule: an entity carrying an unresolved identity conflict.
    conflicted = conn.execute(
        """
        SELECT COUNT(*)
        FROM cdp.golden_entity
        WHERE has_conflict = TRUE
          AND entity_status <> 'MERGED'
        """
    ).fetchone()[0]

    if conflicted:
        warning.append(f"{conflicted} golden entity(ies) carry an unresolved conflict")

    # --- Score bands.
    if overall is None:
        blocking.append("overall score could not be computed")
    elif overall < BLOCK_SCORE:
        blocking.append(f"overall score {overall:.1f} is below {BLOCK_SCORE:.0f}")
    elif overall < WARN_SCORE:
        warning.append(f"overall score {overall:.1f} is below {WARN_SCORE:.0f}")

    # --- Coverage: entities that were never assessed.
    if total_entities and assessed < total_entities:
        warning.append(
            f"{total_entities - assessed} of {total_entities} entities not assessed"
        )

    # --- Open HIGH-severity reviews.
    high_open = conn.execute(
        """
        SELECT COUNT(*)
        FROM cdp.review_queue
        WHERE status = 'OPEN' AND severity = 'HIGH'
        """
    ).fetchone()[0]

    if high_open:
        warning.append(f"{high_open} HIGH-severity review(s) still open")

    gate_status = "BLOCKED" if blocking else ("WARNING" if warning else "PASSED")

    conn.execute("DELETE FROM cdp.cdp_quality_gate WHERE run_id = ?", [run_id])
    conn.execute(
        """
        INSERT INTO cdp.cdp_quality_gate (
            run_id, gate_status, overall_score, blocking_reasons, warning_reasons
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            run_id,
            gate_status,
            overall,
            "; ".join(blocking) or None,
            "; ".join(warning) or None,
        ],
    )

    print("\n=== CDP QUALITY GATE ===")
    print(f"Run ID        : {run_id}")
    print(f"Overall score : {overall:.1f} ({status})" if overall is not None else "Overall score : n/a")
    print(f"Entity coverage: {assessed}/{total_entities} assessed")
    print(f"Gate status   : {gate_status}")

    if blocking:
        print("\nBlocking:")
        for reason in blocking:
            print(f"  - {reason}")

    if warning:
        print("\nWarnings:")
        for reason in warning:
            print(f"  - {reason}")

    if not blocking and not warning:
        print("\nNo issues found.")

    conn.close()
    return 1 if gate_status == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
