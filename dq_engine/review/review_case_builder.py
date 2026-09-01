"""Group review queue evidence into cases.

cdp.review_queue records one row per detected issue. That is the right
grain for detection and the wrong one for a steward: G000003 raises
three rows - an email attribute conflict and two identity conflicts -
which are all the same question, "are these three records one person?".

A case is the subject those rows are about:

    GOLDEN_ENTITY   the evidence concerns an existing entity
    SOURCE_RECORD   the evidence concerns a record not yet in one

A review whose own golden_id is null but whose source record already
belongs to an entity is attached to that entity, so "should MOB002 join
G000002?" reads as one case rather than as two unrelated possible
matches raised against CRM002 and LOS002 separately.

Case ids are derived from the subject, so rebuilding is idempotent and a
case a steward has already closed keeps its identity. review_queue is
not modified.
"""

from __future__ import annotations

from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "database" / "cdp.duckdb"

# Worst wins when a case carries evidence of mixed severity.
SEVERITY_ORDER = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}


def case_id_for(subject_type: str, subject_key: str) -> str:
    """Stable id for a case, derived from what it is about."""
    return str(uuid5(NAMESPACE_URL, f"review-case|{subject_type}|{subject_key}"))


def worst(severities) -> str:
    ranked = [s for s in severities if s in SEVERITY_ORDER]
    if not ranked:
        return "MEDIUM"
    return max(ranked, key=lambda s: SEVERITY_ORDER[s])


def load_evidence(conn):
    """Every review, with the subject it belongs to resolved."""
    return conn.execute(
        """
        SELECT
            rq.review_id,
            rq.issue_type,
            rq.severity,
            rq.status,
            rq.source_system,
            rq.source_customer_id,
            rq.attribute_name,
            -- The review's own entity, or the one its source record is in.
            COALESCE(rq.golden_id, m.golden_id) AS subject_golden_id
        FROM cdp.review_queue rq
        LEFT JOIN cdp.golden_entity_member m
               ON m.source_system = rq.source_system
              AND m.source_customer_id = rq.source_customer_id
        ORDER BY rq.review_id
        """
    ).fetchall()


def build(conn) -> dict[str, int]:
    evidence = load_evidence(conn)

    cases: dict[tuple[str, str], dict] = {}

    for (
        review_id,
        issue_type,
        severity,
        status,
        source_system,
        source_customer_id,
        attribute_name,
        subject_golden_id,
    ) in evidence:

        if subject_golden_id:
            subject = ("GOLDEN_ENTITY", subject_golden_id)
        elif source_customer_id:
            subject = ("SOURCE_RECORD", f"{source_system}:{source_customer_id}")
        else:
            # Nothing to hang it on; keep it visible as its own case
            # rather than dropping the evidence.
            subject = ("REVIEW", review_id)

        entry = cases.setdefault(
            subject,
            {
                "members": [],
                "severities": [],
                "issue_types": set(),
                "open": 0,
            },
        )

        entry["members"].append((review_id, issue_type, severity))
        entry["severities"].append(severity)
        entry["issue_types"].add(issue_type)
        if status == "OPEN":
            entry["open"] += 1

    # Rebuild membership from scratch; it is derived, unlike case status.
    conn.execute("DELETE FROM cdp.review_case_member")

    seen: set[str] = set()

    for (subject_type, subject_key), entry in sorted(cases.items()):

        case_id = case_id_for(subject_type, subject_key)
        seen.add(case_id)

        severity = worst(entry["severities"])
        status = "OPEN" if entry["open"] else "RESOLVED"
        issue_types = ", ".join(sorted(entry["issue_types"]))
        summary = (
            f"{len(entry['members'])} issue(s) on {subject_key}: {issue_types}"
        )

        conn.execute(
            """
            INSERT INTO cdp.review_case (
                case_id, subject_type, subject_key, severity, status,
                evidence_count, issue_types, summary, closed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CASE WHEN ? = 'RESOLVED' THEN now() END)
            ON CONFLICT (case_id) DO UPDATE SET
                severity = EXCLUDED.severity,
                status = EXCLUDED.status,
                evidence_count = EXCLUDED.evidence_count,
                issue_types = EXCLUDED.issue_types,
                summary = EXCLUDED.summary,
                updated_at = now(),
                closed_at = CASE
                    WHEN EXCLUDED.status = 'RESOLVED'
                        THEN COALESCE(cdp.review_case.closed_at, now())
                    ELSE NULL
                END
            """,
            [
                case_id,
                subject_type,
                subject_key,
                severity,
                status,
                len(entry["members"]),
                issue_types,
                summary,
                status,
            ],
        )

        for review_id, issue_type, member_severity in entry["members"]:
            conn.execute(
                """
                INSERT INTO cdp.review_case_member (
                    case_id, review_id, issue_type, severity
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT (case_id, review_id) DO NOTHING
                """,
                [case_id, review_id, issue_type, member_severity],
            )

    # A case whose evidence has gone is closed, not deleted: it records
    # that the question was once asked.
    if seen:
        placeholders = ", ".join("?" for _ in seen)
        conn.execute(
            f"""
            UPDATE cdp.review_case
            SET status = 'RESOLVED',
                evidence_count = 0,
                updated_at = now(),
                closed_at = COALESCE(closed_at, now())
            WHERE case_id NOT IN ({placeholders}) AND status <> 'RESOLVED'
            """,
            list(seen),
        )

    return {"cases": len(cases), "evidence": len(evidence)}


def main() -> int:
    conn = duckdb.connect(str(DB_PATH))

    stats = build(conn)

    print("\n=== REVIEW CASES ===")
    print(f"{stats['cases']} case(s) from {stats['evidence']} piece(s) of evidence")

    print(
        conn.sql(
            """
            SELECT
                subject_type,
                subject_key,
                severity,
                status,
                evidence_count,
                issue_types
            FROM cdp.review_case
            ORDER BY status, severity, subject_key
            """
        )
    )

    print("\n=== CASE EVIDENCE ===")
    print(
        conn.sql(
            """
            SELECT
                c.subject_key,
                m.issue_type,
                m.severity,
                rq.status,
                COALESCE(rq.source_system || ':' || rq.source_customer_id, '-')
                    AS source,
                rq.attribute_name
            FROM cdp.review_case_member m
            JOIN cdp.review_case c ON c.case_id = m.case_id
            JOIN cdp.review_queue rq ON rq.review_id = m.review_id
            ORDER BY c.subject_key, m.issue_type
            """
        )
    )

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
