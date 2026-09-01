from pathlib import Path

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "database" / "cdp.duckdb"


def main() -> None:
    conn = duckdb.connect(str(DB_PATH))

    latest_run = conn.execute("""
        SELECT
            run_id,
            dataset
        FROM dq.dq_run
        ORDER BY started_at DESC
        LIMIT 1
    """).fetchone()

    if latest_run is None:
        raise RuntimeError("No DQ run found.")

    run_id, dataset = latest_run

    critical_affected_records = conn.execute("""
        SELECT COUNT(*)
        FROM dq.dq_result
        WHERE run_id = ?
          AND status = 'FAIL'
          AND severity = 'CRITICAL'
    """, [run_id]).fetchone()[0]

    high_affected_records = conn.execute("""
        SELECT COUNT(*)
        FROM dq.dq_result
        WHERE run_id = ?
          AND status = 'FAIL'
          AND severity = 'HIGH'
    """, [run_id]).fetchone()[0]

    medium_affected_records = conn.execute("""
        SELECT COUNT(*)
        FROM dq.dq_result
        WHERE run_id = ?
          AND status = 'FAIL'
          AND severity = 'MEDIUM'
    """, [run_id]).fetchone()[0]

    if critical_affected_records > 0:
        gate_status = "BLOCKED"
    elif high_affected_records > 0:
        gate_status = "WARNING"
    elif medium_affected_records > 0:
        gate_status = "WARNING"
    else:
        gate_status = "PASSED"

    print("\n=== QUALITY GATE ===")
    print(f"Run ID                     : {run_id}")
    print(f"Dataset                    : {dataset}")
    print(
        f"Critical affected records : "
        f"{critical_affected_records}"
    )
    print(
        f"High affected records     : "
        f"{high_affected_records}"
    )
    print(
        f"Medium affected records   : "
        f"{medium_affected_records}"
    )
    print(f"Gate status               : {gate_status}")

    conn.close()


if __name__ == "__main__":
    main()