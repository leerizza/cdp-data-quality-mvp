import duckdb

conn = duckdb.connect("../../database/cdp.duckdb")

query = """
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
ORDER BY rule_id, customer_id
"""

print(conn.sql(query))

conn.close()