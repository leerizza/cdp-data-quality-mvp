import duckdb

conn = duckdb.connect("../../database/cdp.duckdb")

query = """
SELECT
    rule_id,
    dataset,
    is_active,
    test_sql
FROM dq.dq_rule_master
ORDER BY rule_id
"""

print(conn.sql(query))

conn.close()