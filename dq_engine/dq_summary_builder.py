from pathlib import Path

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "database" / "cdp.duckdb"


def main() -> None:
    conn = duckdb.connect(str(DB_PATH))

    latest_run = conn.execute("""
        SELECT
            run_id,
            dataset,
            total_records
        FROM dq.dq_run
        ORDER BY started_at DESC
        LIMIT 1
    """).fetchone()

    if latest_run is None:
        raise RuntimeError("No DQ run found.")

    run_id, dataset, total_records = latest_run

    print(f"Run ID : {run_id}")
    print(f"Dataset: {dataset}")
    print(f"Records: {total_records}")

    # Remove summary for this run so the script is idempotent.
    conn.execute(
        """
        DELETE FROM dq.dq_summary
        WHERE run_id = ?
        """,
        [run_id],
    )

    rules = conn.execute("""
        SELECT
            rule_id,
            dimension,
            severity,
            threshold,
            metric_type
        FROM dq.dq_rule_master
        WHERE dataset = ?
          AND is_active = TRUE
        ORDER BY rule_id
    """, [dataset]).fetchall()

    for (
        rule_id,
        dimension,
        severity,
        threshold,
        metric_type,
    ) in rules:

        failed_records = conn.execute(
            """
            SELECT COUNT(*)
            FROM dq.dq_result
            WHERE run_id = ?
              AND rule_id = ?
              AND status = 'FAIL'
            """,
            [run_id, rule_id],
        ).fetchone()[0]

        pass_rate = (
            ((total_records - failed_records) / total_records) * 100
            if total_records
            else 100.0
        )

        failure_rate = (
            (failed_records / total_records) * 100
            if total_records
            else 0.0
        )

        if metric_type == "PASS_RATE":
            status = (
                "PASS"
                if pass_rate >= threshold
                else "FAIL"
            )

        elif metric_type == "FAILURE_RATE":
            status = (
                "PASS"
                if failure_rate <= threshold
                else "FAIL"
            )

        else:
            status = "UNKNOWN"

        conn.execute(
            """
            INSERT INTO dq.dq_summary (
                run_id,
                dataset,
                dimension,
                total_records,
                failed_records,
                pass_rate,
                status,
                rule_id,
                severity,
                threshold,
                metric_type
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                run_id,
                dataset,
                dimension,
                total_records,
                failed_records,
                pass_rate,
                status,
                rule_id,
                severity,
                threshold,
                metric_type,
            ],
        )

    result = conn.sql("""
        SELECT
            rule_id,
            dimension,
            severity,
            total_records,
            failed_records,
            ROUND(pass_rate, 2) AS pass_rate,
            threshold,
            metric_type,
            status
        FROM dq.dq_summary
        WHERE run_id = ?
        ORDER BY rule_id
    """, params=[run_id])

    print("\n=== DQ SUMMARY ===")
    print(result)

    conn.close()


if __name__ == "__main__":
    main()