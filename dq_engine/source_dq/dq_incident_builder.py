from datetime import datetime, timezone
from pathlib import Path

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "database" / "cdp.duckdb"


def generate_incident_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    return f"INC-{timestamp}"


def main() -> None:
    conn = duckdb.connect(str(DB_PATH))

    latest_run = conn.execute("""
        SELECT run_id
        FROM dq.dq_run
        ORDER BY started_at DESC
        LIMIT 1
    """).fetchone()

    if latest_run is None:
        raise RuntimeError("No DQ run found.")

    run_id = latest_run[0]

    # Make the operation idempotent.
    conn.execute(
        """
        DELETE FROM dq.dq_incident
        WHERE run_id = ?
        """,
        [run_id],
    )

    failed_results = conn.execute("""
        SELECT
            rule_id,
            record_id,
            severity,
            column_name,
            message
        FROM dq.dq_result
        WHERE run_id = ?
          AND status = 'FAIL'
        ORDER BY rule_id, record_id
    """, [run_id]).fetchall()

    for (
        rule_id,
        record_id,
        severity,
        column_name,
        message,
    ) in failed_results:

        incident_id = generate_incident_id()

        full_message = (
            f"{message}; "
            f"column={column_name}; "
            f"record_id={record_id}"
        )

        conn.execute(
            """
            INSERT INTO dq.dq_incident (
                incident_id,
                run_id,
                rule_id,
                record_id,
                severity,
                status,
                message,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                incident_id,
                run_id,
                rule_id,
                record_id,
                severity,
                "OPEN",
                full_message,
                datetime.now(timezone.utc),
            ],
        )

    result = conn.sql("""
        SELECT
            incident_id,
            rule_id,
            record_id,
            severity,
            status,
            message
        FROM dq.dq_incident
        WHERE run_id = ?
        ORDER BY rule_id, record_id
    """, params=[run_id])

    print("\n=== DQ INCIDENTS ===")
    print(result)

    conn.close()


if __name__ == "__main__":
    main()