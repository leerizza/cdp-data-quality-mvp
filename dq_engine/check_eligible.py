import duckdb

conn = duckdb.connect("../../database/cdp.duckdb")

query = """
SELECT
    customer_id,
    nik,
    full_name,
    phone,
    email,
    birth_date
FROM main.eligible_customer
ORDER BY customer_id
LIMIT 20
"""

print(conn.sql(query))

conn.close()