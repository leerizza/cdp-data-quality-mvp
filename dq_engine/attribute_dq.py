from pathlib import Path
from datetime import datetime

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "database" / "cdp.duckdb"


def is_valid_nik(value):
    if value is None:
        return False

    value = str(value).strip()

    return (
        len(value) == 16
        and value.isdigit()
    )


def is_valid_phone(value):
    if value is None:
        return False

    value = str(value).strip()

    return (
        value.startswith("08")
        and value.isdigit()
        and 10 <= len(value) <= 15
    )


def is_valid_email(value):
    if value is None:
        return False

    value = str(value).strip().lower()

    return (
        "@" in value
        and "." in value.split("@")[-1]
    )


def is_valid_birth_date(value):
    if value is None:
        return False

    try:
        return value <= datetime.now().date()

    except Exception:
        return False


def validate(attribute, value):

    if value is None or str(value).strip() == "":
        return False, "NULL_OR_EMPTY"

    if attribute == "nik":
        return (
            is_valid_nik(value),
            "VALID_NIK"
            if is_valid_nik(value)
            else "INVALID_NIK",
        )

    if attribute == "phone":
        return (
            is_valid_phone(value),
            "VALID_PHONE"
            if is_valid_phone(value)
            else "INVALID_PHONE",
        )

    if attribute == "email":
        return (
            is_valid_email(value),
            "VALID_EMAIL"
            if is_valid_email(value)
            else "INVALID_EMAIL",
        )

    if attribute == "birth_date":
        return (
            is_valid_birth_date(value),
            "VALID_BIRTH_DATE"
            if is_valid_birth_date(value)
            else "INVALID_BIRTH_DATE",
        )

    # full_name only needs non-empty for this MVP.
    if attribute == "full_name":
        return True, "VALID_FULL_NAME"

    return True, "NO_RULE"


def main():

    conn = duckdb.connect(str(DB_PATH))

    run_id = conn.execute("""
        SELECT run_id
        FROM dq.dq_run
        WHERE dataset = 'stg_customer'
        ORDER BY started_at DESC
        LIMIT 1
    """).fetchone()

    if run_id:
        run_id = run_id[0]
    else:
        run_id = f"ATTR-{datetime.now():%Y%m%d%H%M%S}"

    conn.execute("""
        DELETE FROM dq.dq_attribute_result
        WHERE run_id = ?
    """, [run_id])

    attributes = [
        "nik",
        "full_name",
        "phone",
        "email",
        "birth_date",
    ]

    rows = conn.execute("""
        SELECT
            source_system,
            source_customer_id,
            nik,
            full_name,
            phone,
            email,
            birth_date
        FROM main.customer_unified
        ORDER BY source_system, source_customer_id
    """).fetchall()

    for row in rows:

        (
            source_system,
            source_customer_id,
            nik,
            full_name,
            phone,
            email,
            birth_date,
        ) = row

        values = {
            "nik": nik,
            "full_name": full_name,
            "phone": phone,
            "email": email,
            "birth_date": birth_date,
        }

        for attribute in attributes:

            value = values[attribute]

            valid, message = validate(
                attribute,
                value,
            )

            status = (
                "PASS"
                if valid
                else "FAIL"
            )

            conn.execute(
                """
                INSERT INTO dq.dq_attribute_result (
                    run_id,
                    source_system,
                    source_customer_id,
                    attribute_name,
                    status,
                    rule_id,
                    message
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    run_id,
                    source_system,
                    source_customer_id,
                    attribute,
                    status,
                    None,
                    message,
                ],
            )

    print("\n=== ATTRIBUTE DQ ===")

    print(
        conn.sql("""
            SELECT
                source_system,
                source_customer_id,
                attribute_name,
                status,
                message
            FROM dq.dq_attribute_result
            WHERE run_id = (
                SELECT MAX(run_id)
                FROM dq.dq_attribute_result
            )
            ORDER BY
                source_system,
                source_customer_id,
                attribute_name
        """)
    )

    conn.close()


if __name__ == "__main__":
    main()