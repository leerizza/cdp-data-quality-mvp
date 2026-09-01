from datetime import datetime, timezone
from pathlib import Path

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[2]
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

    # For this MVP, quarantine applies to stg_customer.
    if dataset != "stg_customer":
        raise ValueError(
            f"Unsupported dataset: {dataset}"
        )

    # Idempotent execution.
    conn.execute(
        """
        DELETE FROM dq.quarantine_customer
        WHERE run_id = ?
        """,
        [run_id],
    )

    failed_records = conn.execute("""
        SELECT
            rule_id,
            record_id,
            column_name,
            severity,
            message
        FROM dq.dq_result
        WHERE run_id = ?
          AND status = 'FAIL'
        ORDER BY rule_id, record_id
    """, [run_id]).fetchall()

    for (
        rule_id,
        record_id,
        column_name,
        severity,
        message,
    ) in failed_records:

        # Retrieve the original value.
        value_result = conn.execute(
            f"""
            SELECT "{column_name}"
            FROM main.{dataset}
            WHERE customer_id = ?
            """,
            [record_id],
        )

        value_row = value_result.fetchone()

        original_value = None

        if value_row is not None:
            value = value_row[0]

            if value is not None:
                original_value = str(value)

        incident = conn.execute(
            """
            SELECT incident_id
            FROM dq.dq_incident
            WHERE run_id = ?
              AND rule_id = ?
              AND record_id = ?
            LIMIT 1
            """,
            [run_id, rule_id, record_id],
        ).fetchone()

        incident_id = (
            incident[0]
            if incident is not None
            else None
        )

        conn.execute(
            """
            INSERT INTO dq.quarantine_customer (
                run_id,
                incident_id,
                rule_id,
                customer_id,
                severity,
                column_name,
                original_value,
                reason,
                status,
                quarantined_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                run_id,
                incident_id,
                rule_id,
                record_id,
                severity,
                column_name,
                original_value,
                message,
                "QUARANTINED",
                datetime.now(timezone.utc),
            ],
        )

    result = conn.sql("""
        SELECT
            run_id,
            customer_id,
            rule_id,
            severity,
            column_name,
            original_value,
            reason,
            status
        FROM dq.quarantine_customer
        WHERE run_id = ?
        ORDER BY rule_id, customer_id
    """, params=[run_id])

    print("\n=== QUARANTINE ===")
    print(result)

    conn.close()


if __name__ == "__main__":
    main()