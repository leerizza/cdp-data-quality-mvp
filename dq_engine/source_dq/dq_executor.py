from datetime import datetime, timezone
from pathlib import Path

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "database" / "cdp.duckdb"

DATASET = "stg_customer"


def generate_run_id() -> str:
    return datetime.now(timezone.utc).strftime("RUN-%Y%m%d-%H%M%S")


def main() -> None:
    conn = duckdb.connect(str(DB_PATH))

    run_id = generate_run_id()
    started_at = datetime.now(timezone.utc)

    total_records = conn.execute(
        f"SELECT COUNT(*) FROM main.{DATASET}"
    ).fetchone()[0]

    conn.execute(
        """
        INSERT INTO dq.dq_run (
            run_id,
            dataset,
            started_at,
            status,
            total_records
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            run_id,
            DATASET,
            started_at,
            "RUNNING",
            total_records,
        ],
    )

    rules = conn.execute(
        """
        SELECT
            rule_id,
            column_name,
            dimension,
            severity,
            threshold,
            metric_type,
            test_sql
        FROM dq.dq_rule_master
        WHERE dataset = ?
          AND is_active = TRUE
        ORDER BY rule_id
        """,
        [DATASET],
    ).fetchall()

    failed_rule_count = 0

    for (
        rule_id,
        column_name,
        dimension,
        severity,
        threshold,
        metric_type,
        test_sql,
    ) in rules:

        if not test_sql:
            continue

        query = f"""
            SELECT *
            FROM main.{DATASET}
            WHERE {test_sql}
        """

        result = conn.execute(query)

        failed_records = result.fetchall()

        columns = [
            column[0]
            for column in result.description
        ]

        record_id_index = columns.index("customer_id")

        if failed_records:
            failed_rule_count += 1

        for row in failed_records:

            record_id = row[record_id_index]

            message = (
                f"Rule failed: {rule_id}"
            )

            conn.execute(
                """
                INSERT INTO dq.dq_result (
                    run_id,
                    rule_id,
                    record_id,
                    column_name,
                    status,
                    severity,
                    message,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    run_id,
                    rule_id,
                    str(record_id),
                    column_name,
                    "FAIL",
                    severity,
                    message,
                    datetime.now(timezone.utc),
                ],
            )

    finished_at = datetime.now(timezone.utc)

    final_status = (
        "FAILED"
        if failed_rule_count > 0
        else "PASSED"
    )

    conn.execute(
        """
        UPDATE dq.dq_run
        SET
            finished_at = ?,
            status = ?
        WHERE run_id = ?
        """,
        [
            finished_at,
            final_status,
            run_id,
        ],
    )

    print(f"Run ID: {run_id}")
    print(f"Dataset: {DATASET}")
    print(f"Records: {total_records}")
    print(f"Failed rules: {failed_rule_count}")
    print(f"Status: {final_status}")

    conn.close()


if __name__ == "__main__":
    main()