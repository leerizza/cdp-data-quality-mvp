from pathlib import Path
from uuid import uuid4

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "database" / "cdp.duckdb"

ATTRIBUTES = [
    "nik",
    "phone",
    "email",
    "birth_date",
    "full_name",
]

HARD_CONFLICT_ATTRIBUTES = {
    "nik",
    "phone",
    "email",
    "birth_date",
}


def normalize(value):
    if value is None:
        return None

    value = str(value).strip().lower()

    if value == "":
        return None

    return value


def main():
    conn = duckdb.connect(str(DB_PATH))

    conn.execute(
        """
        DELETE FROM cdp.cross_source_consistency
        """
    )

    entities = conn.execute(
        """
        SELECT
            golden_id
        FROM cdp.golden_entity
        WHERE entity_status IN ('ACTIVE', 'REVIEW')
        ORDER BY golden_id
        """
    ).fetchall()

    for (golden_id,) in entities:

        members = conn.execute(
            """
            SELECT
                source_system,
                source_customer_id
            FROM cdp.golden_entity_member
            WHERE golden_id = ?
            ORDER BY source_system
            """,
            [golden_id],
        ).fetchall()

        for attribute in ATTRIBUTES:

            values = []

            for (
                source_system,
                source_customer_id,
            ) in members:

                row = conn.execute(
                    f"""
                    SELECT "{attribute}"
                    FROM main.customer_unified
                    WHERE source_system = ?
                      AND source_customer_id = ?
                    """,
                    [
                        source_system,
                        source_customer_id,
                    ],
                ).fetchone()

                if row is None:
                    continue

                value = normalize(row[0])

                if value is None:
                    continue

                values.append(
                    (
                        source_system,
                        source_customer_id,
                        value,
                    )
                )

            distinct_values = sorted(
                set(
                    value
                    for _, _, value in values
                )
            )

            distinct_count = len(
                distinct_values
            )

            if distinct_count <= 1:

                consistency_status = (
                    "CONSISTENT"
                )

                severity = "NONE"

            else:

                if attribute in HARD_CONFLICT_ATTRIBUTES:

                    consistency_status = (
                        "CONFLICT"
                    )

                    severity = "HIGH"

                else:

                    consistency_status = (
                        "VARIATION"
                    )

                    severity = "LOW"

            source_values = "; ".join(
                f"{source}:{record}={value}"
                for source, record, value
                in values
            )

            conn.execute(
                """
                INSERT INTO cdp.cross_source_consistency (
                    consistency_id,
                    golden_id,
                    attribute_name,
                    distinct_value_count,
                    consistency_status,
                    source_values,
                    severity
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    str(uuid4()),
                    golden_id,
                    attribute,
                    distinct_count,
                    consistency_status,
                    source_values,
                    severity,
                ],
            )

    print(
        "\n=== CROSS-SOURCE CONSISTENCY ==="
    )

    print(
        conn.sql(
            """
            SELECT
                golden_id,
                attribute_name,
                distinct_value_count,
                consistency_status,
                severity,
                source_values
            FROM cdp.cross_source_consistency
            ORDER BY
                golden_id,
                attribute_name
            """
        )
    )

    print(
        "\n=== CONSISTENCY SUMMARY ==="
    )

    print(
        conn.sql(
            """
            SELECT
                consistency_status,
                severity,
                COUNT(*) AS total
            FROM cdp.cross_source_consistency
            GROUP BY
                consistency_status,
                severity
            ORDER BY
                consistency_status,
                severity
            """
        )
    )

    conn.close()


if __name__ == "__main__":
    main()