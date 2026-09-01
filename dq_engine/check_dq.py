import duckdb

DB_PATH = "../../database/cdp.duckdb"

conn = duckdb.connect(DB_PATH)

query = """
SELECT
    table_schema,
    table_name
FROM information_schema.tables
WHERE table_schema = 'dq'
ORDER BY table_name
"""

result = conn.sql(query)

print(result)

conn.close()