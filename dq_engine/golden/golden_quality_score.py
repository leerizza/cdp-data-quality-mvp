from pathlib import Path

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "database" / "cdp.duckdb"

ATTRIBUTES = [
    "nik",
    "full_name",
    "phone",
    "email",
    "birth_date",
]


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


def main():
    conn = duckdb.connect(str(DB_PATH))

    thresholds = load_thresholds(conn, "golden_quality")
    identity_scores = {
        "HIGH": thresholds["identity_score_high"],
        "MEDIUM": thresholds["identity_score_medium"],
        "LOW": thresholds["identity_score_low"],
    }
    print(f"Loaded golden quality thresholds from {THRESHOLD_CSV.name}")

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

        total_attributes = len(ATTRIBUTES)

        # An entity that never reached survivorship (typically REVIEW)
        # has no surviving attributes. Scoring it as 0% completeness
        # would report "14% quality" when the truth is "not scored yet",
        # so record it as NOT_ASSESSED with no score at all.
        if not attribute_rows:
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
                VALUES (?, ?, 0, ?, NULL, NULL, ?, ?, NULL, 'NOT_ASSESSED')
                """,
                [
                    golden_id,
                    total_attributes,
                    total_attributes,
                    identity_confidence,
                    has_conflict,
                ],
            )
            continue

        attribute_map = {
            row[0]: row
            for row in attribute_rows
        }

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

        identity_score = identity_scores.get(
            identity_confidence,
            thresholds["identity_score_low"],
        )

        quality_score = (
            completeness_score * thresholds["weight_completeness"]
            + validity_score * thresholds["weight_validity"]
            + identity_score * thresholds["weight_identity"]
        )

        if has_conflict:
            quality_status = "REVIEW"

        elif quality_score >= thresholds["band_excellent"]:
            quality_status = "EXCELLENT"

        elif quality_score >= thresholds["band_good"]:
            quality_status = "GOOD"

        elif quality_score >= thresholds["band_warning"]:
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