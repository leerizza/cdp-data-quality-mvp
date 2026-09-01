from pathlib import Path
from uuid import uuid4

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "database" / "cdp.duckdb"


def normalize(value):
    if value is None:
        return None

    value = str(value).strip().lower()

    if value == "":
        return None

    return value


def main() -> None:
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
        DELETE FROM cdp.identity_match_candidate
    """)

    candidate_count = 0

    for i, left in enumerate(records):

        for j, right in enumerate(records):

            # Avoid self-match and duplicate reverse pairs.
            if j <= i:
                continue

            if (
                left["source_system"]
                == right["source_system"]
            ):
                continue

            left_nik = normalize(left["nik"])
            right_nik = normalize(right["nik"])

            left_phone = normalize(left["phone"])
            right_phone = normalize(right["phone"])

            left_email = normalize(left["email"])
            right_email = normalize(right["email"])

            left_dob = left["birth_date"]
            right_dob = right["birth_date"]

            nik_match = (
                left_nik is not None
                and right_nik is not None
                and left_nik == right_nik
            )

            phone_match = (
                left_phone is not None
                and right_phone is not None
                and left_phone == right_phone
            )

            email_match = (
                left_email is not None
                and right_email is not None
                and left_email == right_email
            )

            dob_match = (
                left_dob is not None
                and right_dob is not None
                and left_dob == right_dob
            )

            dob_conflict = (
                left_dob is not None
                and right_dob is not None
                and left_dob != right_dob
            )

            phone_conflict = (
                left_phone is not None
                and right_phone is not None
                and left_phone != right_phone
            )

            email_conflict = (
                left_email is not None
                and right_email is not None
                and left_email != right_email
            )

            # --------------------------------------------------
            # Blocking strategy
            #
            # Candidate if at least one strong identity signal
            # is available.
            # --------------------------------------------------

            candidate = (
                nik_match
                or phone_match
                or email_match
                or (phone_match and dob_match)
                or (email_match and dob_match)
            )

            if not candidate:
                continue

            score = 0

            if nik_match:
                score += 100

            if dob_match:
                score += 30

            if phone_match:
                score += 30

            if email_match:
                score += 30

            if dob_conflict:
                score -= 50

            if phone_conflict:
                score -= 30

            if email_conflict:
                score -= 30

            candidate_id = str(uuid4())

            conn.execute(
                """
                INSERT INTO cdp.identity_match_candidate (
                    candidate_id,
                    source_system,
                    source_customer_id,
                    candidate_source_system,
                    candidate_source_customer_id,

                    nik_match,
                    dob_match,
                    phone_match,
                    email_match,

                    dob_conflict,
                    phone_conflict,
                    email_conflict,

                    score
                )
                VALUES (
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?
                )
                """,
                [
                    candidate_id,

                    left["source_system"],
                    left["source_customer_id"],

                    right["source_system"],
                    right["source_customer_id"],

                    nik_match,
                    dob_match,
                    phone_match,
                    email_match,

                    dob_conflict,
                    phone_conflict,
                    email_conflict,

                    score,
                ],
            )

            candidate_count += 1

    print("\n=== CANDIDATE GENERATION ===")
    print(f"Source records : {len(records)}")
    print(f"Candidate pairs: {candidate_count}")

    print("\n=== CANDIDATES ===")

    print(
        conn.sql("""
            SELECT
                source_system,
                source_customer_id,
                candidate_source_system,
                candidate_source_customer_id,
                nik_match,
                dob_match,
                phone_match,
                email_match,
                dob_conflict,
                phone_conflict,
                email_conflict,
                score
            FROM cdp.identity_match_candidate
            ORDER BY
                source_customer_id,
                candidate_source_customer_id
        """)
    )

    conn.close()


if __name__ == "__main__":
    main()