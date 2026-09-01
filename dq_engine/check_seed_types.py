import duckdb

conn = duckdb.connect("../../database/cdp.duckdb", read_only=True)

for table in [
    "customer",
    "crm_customer",
    "los_customer",
    "mobile_customer",
]:
    print(f"\n=== {table} ===")

    print(
        conn.sql(
            f"DESCRIBE main.{table}"
        )
    )

conn.close()