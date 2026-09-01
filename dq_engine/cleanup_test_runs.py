import duckdb


conn = duckdb.connect("../../database/cdp.duckdb")

conn.execute("""
    DELETE FROM dq.dq_result
    WHERE run_id = 'RUN-20260831-075524'
""")

conn.execute("""
    DELETE FROM dq.dq_run
    WHERE run_id = 'RUN-20260831-075524'
""")

conn.close()

print("Test run removed.")