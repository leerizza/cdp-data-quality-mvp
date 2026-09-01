import duckdb

conn = duckdb.connect(
    "../../database/cdp.duckdb",
    read_only=True,
)

print("\n=== GOLDEN QUALITY OVERVIEW ===")

print(
    conn.sql(
        """
        SELECT
            quality_status,
            COUNT(*) AS golden_count,
            ROUND(AVG(quality_score), 2)
                AS avg_quality_score
        FROM cdp.golden_quality_score
        GROUP BY quality_status
        ORDER BY quality_status
        """
    )
)

print("\n=== GOLDEN QUALITY DETAIL ===")

print(
    conn.sql(
        """
        SELECT
            golden_id,
            ROUND(quality_score, 2)
                AS quality_score,
            quality_status,
            completeness_score,
            validity_score,
            identity_confidence,
            has_conflict
        FROM cdp.golden_quality_score
        ORDER BY golden_id
        """
    )
)

conn.close()