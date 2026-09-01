import duckdb

conn = duckdb.connect("../../database/cdp.duckdb", read_only=True)

print("=== ALL TABLES ===")

print(
    conn.sql("""
        SELECT
            table_schema,
            table_name
        FROM information_schema.tables
        WHERE table_name LIKE '%identity%'
        ORDER BY table_schema, table_name
    """)
)

conn.close()