import duckdb

conn = duckdb.connect("../../database/cdp.duckdb", read_only=True)

queries = {
    "CRM SEED": """
        SELECT
            source_customer_id,
            nik,
            full_name
        FROM main.crm_customer
        ORDER BY source_customer_id
    """,
    "CRM STAGING": """
        SELECT
            source_customer_id,
            nik,
            full_name
        FROM main.stg_crm_customer
        ORDER BY source_customer_id
    """,
    "UNIFIED": """
        SELECT
            source_system,
            source_customer_id,
            nik,
            full_name
        FROM main.customer_unified
        WHERE source_system = 'CRM'
        ORDER BY source_customer_id
    """,
}

for name, query in queries.items():
    print(f"\n=== {name} ===")
    print(conn.sql(query))

conn.close()