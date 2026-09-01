from pathlib import Path
from uuid import uuid4

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "database" / "cdp.duckdb"


def determine_decision(row):
    (
        nik_match,
        dob_match,
        phone_match,
        email_match,
        dob_conflict,
        phone_conflict,
        email_conflict,
        score,
    ) = row

    strong_conflict = (
        dob_conflict
        or phone_conflict
        or email_conflict
    )

    # ---------------------------------------------------------
    # Conflict takes precedence over score.
    # ---------------------------------------------------------
    if nik_match and strong_conflict:
        return "CONFLICT"

    # ---------------------------------------------------------
    # Strong deterministic match.
    # ---------------------------------------------------------
    if nik_match and score >= 100:
        return "MATCH"

    # ---------------------------------------------------------
    # Multiple supporting identity signals.
    # ---------------------------------------------------------
    supporting_signals = sum([
        bool(dob_match),
        bool(phone_match),
        bool(email_match),
    ])

    if supporting_signals >= 2 and not strong_conflict:
        return "POSSIBLE_MATCH"

    # ---------------------------------------------------------
    # Moderate evidence.
    # ---------------------------------------------------------
    if score >= 60 and not strong_conflict:
        return "POSSIBLE_MATCH"

    return "NO_MATCH"


def main():
    conn = duckdb.connect(str(DB_PATH))

    rows = conn.execute("""
        SELECT
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
        FROM cdp.identity_match_candidate
        ORDER BY
            source_customer_id,
            candidate_source_customer_id
    """).fetchall()

    conn.execute("""
        DELETE FROM cdp.identity_match
    """)

    for row in rows:

        (
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

            score,
        ) = row

        status = determine_decision([
            nik_match,
            dob_match,
            phone_match,
            email_match,
            dob_conflict,
            phone_conflict,
            email_conflict,
            score,
        ])

        matched_on = []

        if nik_match:
            matched_on.append("NIK")

        if dob_match:
            matched_on.append("DOB")

        if phone_match:
            matched_on.append("PHONE")

        if email_match:
            matched_on.append("EMAIL")

        conflicts = []

        if dob_conflict:
            conflicts.append("DOB")

        if phone_conflict:
            conflicts.append("PHONE")

        if email_conflict:
            conflicts.append("EMAIL")

        if conflicts:
            matched_on.append(
                "CONFLICT:" + ",".join(conflicts)
            )

        reason = (
            f"Candidate score={score}; "
            f"signals={','.join(matched_on)}"
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
                reason,
                candidate_source_system,
                candidate_source_customer_id
            )
            VALUES (
                ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?
            )
            """,
            [
                match_id,
                source_system,
                source_customer_id,
                None,
                status,
                score,
                status,
                ",".join(matched_on),
                reason,
                candidate_source_system,
                candidate_source_customer_id,
            ],
        )

    print("\n=== IDENTITY PAIR DECISIONS ===")

    print(
        conn.sql("""
            SELECT
                source_system,
                source_customer_id,
                candidate_source_system,
                candidate_source_customer_id,
                match_type,
                match_score,
                matched_on,
                reason
            FROM cdp.identity_match
            ORDER BY
                source_customer_id,
                candidate_source_customer_id
        """)
    )

    conn.close()


if __name__ == "__main__":
    main()