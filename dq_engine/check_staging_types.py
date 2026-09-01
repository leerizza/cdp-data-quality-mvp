import duckdb

conn = duckdb.connect("../../database/cdp.duckdb", read_only=True)

for table in [
    "stg_crm_customer",
    "stg_los_customer",
    "stg_mobile_customer",
    "customer_unified",
]:
    print(f"\n=== {table} ===")

    print(
        conn.sql(
            f"""
            DESCRIBE main.{table}
            """
        )
    )

conn.close()