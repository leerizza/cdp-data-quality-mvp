from pathlib import Path

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "database" / "cdp.duckdb"

ATTRIBUTES = [
    "nik",
    "full_name",
    "phone",
    "email",
    "birth_date",
]


IDENTITY_SCORE = {
    "HIGH": 100.0,
    "MEDIUM": 70.0,
    "LOW": 40.0,
}


def main():
    conn = duckdb.connect(str(DB_PATH))

    conn.execute(
        """
        DELETE FROM cdp.golden_quality_score
        """
    )

    entities = conn.execute(
        """
        SELECT
            golden_id,
            confidence,
            has_conflict
        FROM cdp.golden_entity
        WHERE entity_status IN ('ACTIVE', 'REVIEW')
        ORDER BY golden_id
        """
    ).fetchall()

    for (
        golden_id,
        identity_confidence,
        has_conflict,
    ) in entities:

        attribute_rows = conn.execute(
            """
            SELECT
                attribute_name,
                attribute_value,
                dq_eligible
            FROM cdp.golden_customer_attribute
            WHERE golden_id = ?
            """,
            [golden_id],
        ).fetchall()

        attribute_map = {
            row[0]: row
            for row in attribute_rows
        }

        total_attributes = len(ATTRIBUTES)
        valid_attributes = 0
        missing_attributes = 0

        for attribute in ATTRIBUTES:

            row = attribute_map.get(attribute)

            if row is None:
                missing_attributes += 1
                continue

            _, value, dq_eligible = row

            if (
                value is not None
                and dq_eligible
            ):
                valid_attributes += 1
            else:
                missing_attributes += 1

        completeness_score = (
            (
                total_attributes
                - missing_attributes
            )
            / total_attributes
        ) * 100

        validity_score = (
            valid_attributes
            / total_attributes
        ) * 100

        identity_score = IDENTITY_SCORE.get(
            identity_confidence,
            40.0,
        )

        quality_score = (
            completeness_score * 0.40
            + validity_score * 0.40
            + identity_score * 0.20
        )

        if has_conflict:
            quality_status = "REVIEW"

        elif quality_score >= 95:
            quality_status = "EXCELLENT"

        elif quality_score >= 85:
            quality_status = "GOOD"

        elif quality_score >= 70:
            quality_status = "WARNING"

        else:
            quality_status = "POOR"

        conn.execute(
            """
            INSERT INTO cdp.golden_quality_score (
                golden_id,
                total_attributes,
                valid_attributes,
                missing_attributes,
                completeness_score,
                validity_score,
                identity_confidence,
                has_conflict,
                quality_score,
                quality_status
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [
                golden_id,
                total_attributes,
                valid_attributes,
                missing_attributes,
                completeness_score,
                validity_score,
                identity_confidence,
                has_conflict,
                quality_score,
                quality_status,
            ],
        )

    print("\n=== GOLDEN QUALITY SCORE ===")

    print(
        conn.sql(
            """
            SELECT
                golden_id,
                total_attributes,
                valid_attributes,
                missing_attributes,
                ROUND(completeness_score, 2)
                    AS completeness_score,
                ROUND(validity_score, 2)
                    AS validity_score,
                identity_confidence,
                has_conflict,
                ROUND(quality_score, 2)
                    AS quality_score,
                quality_status
            FROM cdp.golden_quality_score
            ORDER BY golden_id
            """
        )
    )

    conn.close()


if __name__ == "__main__":
    main()