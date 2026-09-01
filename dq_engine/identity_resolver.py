from pathlib import Path
from uuid import uuid4

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "database" / "cdp.duckdb"


def normalize_text(value):
    if value is None:
        return None

    return " ".join(
        str(value)
        .lower()
        .strip()
        .split()
    )


def compare_records(left, right):
    signals = []
    score = 0
    conflict = False

    left_nik = left["nik"]
    right_nik = right["nik"]

    if (
        left_nik
        and right_nik
        and left_nik == right_nik
    ):
        signals.append("NIK_EXACT")
        score += 100

    left_dob = left["birth_date"]
    right_dob = right["birth_date"]

    if (
        left_dob is not None
        and right_dob is not None
        and left_dob == right_dob
    ):
        signals.append("DOB_EXACT")
        score += 30

    elif (
        left_dob is not None
        and right_dob is not None
        and left_dob != right_dob
    ):
        conflict = True

    left_phone = left["phone"]
    right_phone = right["phone"]

    if (
        left_phone
        and right_phone
        and left_phone == right_phone
    ):
        signals.append("PHONE_EXACT")
        score += 30

    elif (
        left_phone
        and right_phone
        and left_phone != right_phone
    ):
        conflict = True

    left_email = normalize_text(left["email"])
    right_email = normalize_text(right["email"])

    if (
        left_email
        and right_email
        and left_email == right_email
    ):
        signals.append("EMAIL_EXACT")
        score += 30

    elif (
        left_email
        and right_email
        and left_email != right_email
    ):
        conflict = True

    if conflict and "NIK_EXACT" in signals:
        return {
            "score": score - 100,
            "status": "CONFLICT",
            "matched_on": ",".join(signals),
            "reason": "Strong identity signal with conflicting attributes",
        }

    if score >= 100:
        return {
            "score": score,
            "status": "MATCH",
            "matched_on": ",".join(signals),
            "reason": "Strong deterministic identity match",
        }

    if score >= 60:
        return {
            "score": score,
            "status": "POSSIBLE_MATCH",
            "matched_on": ",".join(signals),
            "reason": "Multiple supporting identity signals",
        }

    return {
        "score": score,
        "status": "NO_MATCH",
        "matched_on": ",".join(signals),
        "reason": "Insufficient identity evidence",
    }


def main():
    conn = duckdb.connect(str(DB_PATH))

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

    columns = [
        "source_system",
        "source_customer_id",
        "nik",
        "full_name",
        "phone",
        "email",
        "birth_date",
    ]

    records = [
        dict(zip(columns, row))
        for row in rows
    ]

    conn.execute("""
        DELETE FROM cdp.identity_match
    """)

    for left in records:

        candidates = [
            right
            for right in records
            if right["source_customer_id"]
            != left["source_customer_id"]
            and right["nik"] is not None
            and left["nik"] is not None
            and right["nik"] == left["nik"]
        ]

        for right in candidates:

            result = compare_records(
                left,
                right,
            )

            match_id = str(uuid4())

            conn.execute(
                """
                INSERT INTO cdp.identity_match (
                    match_id,
                    source_system,
                    source_customer_id,
                    candidate_golden_id,
                    match_type,
                    match_score,
                    match_status,
                    matched_on,
                    reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    match_id,
                    left["source_system"],
                    left["source_customer_id"],
                    None,
                    result["status"],
                    result["score"],
                    result["status"],
                    result["matched_on"],
                    result["reason"],
                ],
            )

    print("\n=== IDENTITY MATCH RESULTS ===")

    print(
        conn.sql("""
            SELECT
                source_system,
                source_customer_id,
                match_type,
                match_score,
                match_status,
                matched_on,
                reason
            FROM cdp.identity_match
            ORDER BY source_customer_id
        """)
    )

    conn.close()


if __name__ == "__main__":
    main()