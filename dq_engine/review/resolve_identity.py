"""Explicit identity-resolution workflow for the review queue.

A data steward names the decision; nothing here picks a review on its own.

The durable output is a row in cdp.identity_resolution_action. Membership in
cdp.golden_entity_member is NOT written here: identity_clustering.py rebuilds
membership from scratch on every run, reading APPROVED actions as trusted
edges, so writing membership directly would be redundant and overwritten.
Re-run the clustering chain to make a decision take effect:

    python main.py --from identity

Usage:
    python dq_engine/resolve_identity.py --list
    python dq_engine/resolve_identity.py --source MOBILE:MOB002 \
        --approve --golden-id G000002 --reason "matching phone, email, DOB"
    python dq_engine/resolve_identity.py --review-id <uuid> --reject \
        --reason "different person, shared household phone"
    python dq_engine/resolve_identity.py --restore-from-audit
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "database" / "cdp.duckdb"
ACTIONS_CSV = PROJECT_ROOT / "metadata" / "identity_resolution_action.csv"

DEFAULT_ACTOR = "DATA_STEWARD"


def list_reviews(conn, status: str) -> None:
    rows = conn.execute(
        """
        SELECT
            review_id,
            issue_type,
            golden_id,
            source_system,
            source_customer_id,
            attribute_name,
            severity,
            status,
            reason
        FROM cdp.review_queue
        WHERE status = ?
        ORDER BY severity, issue_type, source_customer_id
        """,
        [status],
    ).fetchall()

    if not rows:
        print(f"No {status} reviews.")
        return

    print(f"\n=== {status} REVIEWS ({len(rows)}) ===")
    for r in rows:
        source = f"{r[3]}:{r[4]}" if r[3] else "-"
        print(f"\n  review_id : {r[0]}")
        print(f"  issue     : {r[1]}  [{r[6]}]")
        print(f"  golden_id : {r[2]}")
        print(f"  source    : {source}")
        if r[5]:
            print(f"  attribute : {r[5]}")
        print(f"  reason    : {r[8]}")


def find_review(conn, review_id, source):
    """Locate exactly one review by id or by SYSTEM:CUSTOMER_ID."""
    if review_id:
        rows = conn.execute(
            """
            SELECT review_id, issue_type, source_system, source_customer_id, status
            FROM cdp.review_queue
            WHERE review_id = ?
            """,
            [review_id],
        ).fetchall()
        if not rows:
            raise SystemExit(f"No review with id {review_id}")
        return rows[0]

    system, _, customer_id = source.partition(":")
    if not system or not customer_id:
        raise SystemExit("--source must look like MOBILE:MOB002")

    rows = conn.execute(
        """
        SELECT review_id, issue_type, source_system, source_customer_id, status
        FROM cdp.review_queue
        WHERE source_system = ?
          AND source_customer_id = ?
        ORDER BY (status = 'OPEN') DESC
        """,
        [system.upper(), customer_id],
    ).fetchall()

    if not rows:
        raise SystemExit(f"No review found for {system.upper()}:{customer_id}")

    if len(rows) > 1:
        print(f"! {len(rows)} reviews match {system.upper()}:{customer_id}:", file=sys.stderr)
        for r in rows:
            print(f"    {r[0]}  {r[1]}  {r[4]}", file=sys.stderr)
        raise SystemExit("Ambiguous - pass --review-id to pick one.")

    return rows[0]


def assert_golden_entity(conn, golden_id: str) -> None:
    row = conn.execute(
        "SELECT entity_status FROM cdp.golden_entity WHERE golden_id = ?",
        [golden_id],
    ).fetchone()

    if row is None:
        raise SystemExit(f"{golden_id} is not a known Golden Entity.")

    if row[0] != "ACTIVE":
        print(f"! warning: {golden_id} status is {row[0]}, not ACTIVE")


def already_recorded(conn, review_id, action_type, golden_id) -> bool:
    """True when this exact decision is already in the audit trail."""
    row = conn.execute(
        """
        SELECT 1
        FROM cdp.identity_resolution_action
        WHERE review_id = ?
          AND action_type = ?
          AND golden_id IS NOT DISTINCT FROM ?
        """,
        [review_id, action_type, golden_id],
    ).fetchone()
    return row is not None


def resolve(conn, args) -> None:
    review_id, issue_type, source_system, source_customer_id, status = find_review(
        conn, args.review_id, args.source
    )

    action_type = "APPROVED" if args.approve else "REJECTED"
    golden_id = args.golden_id if args.approve else None

    if args.approve:
        if not golden_id:
            raise SystemExit("--approve requires --golden-id")
        assert_golden_entity(conn, golden_id)

    print(f"Review    : {review_id}")
    print(f"Issue     : {issue_type} (currently {status})")
    print(f"Source    : {source_system}:{source_customer_id}")
    print(f"Decision  : {action_type}" + (f" -> {golden_id}" if golden_id else ""))
    print(f"By        : {args.by}")

    if already_recorded(conn, review_id, action_type, golden_id):
        print("\nAlready recorded in the audit trail - nothing to add.")
        return

    conn.execute(
        """
        INSERT INTO cdp.identity_resolution_action (
            action_id,
            review_id,
            action_type,
            source_system,
            source_customer_id,
            golden_id,
            performed_by,
            reason
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            str(uuid4()),
            review_id,
            action_type,
            source_system,
            source_customer_id,
            golden_id,
            args.by,
            args.reason,
        ],
    )

    conn.execute(
        """
        UPDATE cdp.review_queue
        SET status = 'RESOLVED',
            resolution = ?,
            assigned_to = ?,
            resolved_at = CURRENT_TIMESTAMP
        WHERE review_id = ?
        """,
        [action_type, args.by, review_id],
    )

    print("\nRecorded. Re-run the clustering chain to apply it:")
    print("    python main.py --from identity")


def restore_from_audit(conn) -> None:
    """Recreate review_queue rows for audit actions that lost theirs.

    Only reconstructs what identity_resolution_action already proves
    happened; it invents no new decisions.
    """
    orphans = conn.execute(
        """
        SELECT
            a.review_id,
            any_value(a.action_type),
            any_value(a.source_system),
            any_value(a.source_customer_id),
            any_value(a.golden_id),
            any_value(a.performed_by),
            any_value(a.reason)
        FROM cdp.identity_resolution_action a
        LEFT JOIN cdp.review_queue rq
            ON a.review_id = rq.review_id
        WHERE rq.review_id IS NULL
        GROUP BY a.review_id
        """
    ).fetchall()

    if not orphans:
        print("No orphaned audit actions - review_queue is consistent.")
        return

    for (
        review_id,
        action_type,
        source_system,
        source_customer_id,
        golden_id,
        performed_by,
        reason,
    ) in orphans:
        conn.execute(
            """
            INSERT INTO cdp.review_queue (
                review_id,
                issue_type,
                golden_id,
                source_system,
                source_customer_id,
                severity,
                reason,
                status,
                resolution,
                assigned_to,
                resolved_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'RESOLVED', ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT (review_id) DO NOTHING
            """,
            [
                review_id,
                "POSSIBLE_MATCH",
                golden_id,
                source_system,
                source_customer_id,
                "MEDIUM",
                f"[restored from audit] {reason}",
                action_type,
                performed_by,
            ],
        )
        print(f"  restored {review_id}  {source_system}:{source_customer_id} -> {action_type}")

    print(f"\nRestored {len(orphans)} review row(s) from the audit trail.")


def import_actions(conn) -> None:
    """Load steward decisions from the versioned CSV export.

    The database is a build artifact and is not committed, so a fresh
    clone starts with an empty audit trail and the approved matches
    would never be re-applied by clustering. This restores them.
    Existing action_ids are left alone, so re-running is safe.
    """
    if not ACTIONS_CSV.exists():
        raise SystemExit(f"Not found: {ACTIONS_CSV}")

    before = conn.execute(
        "SELECT COUNT(*) FROM cdp.identity_resolution_action"
    ).fetchone()[0]

    conn.execute(
        """
        INSERT INTO cdp.identity_resolution_action (
            action_id, review_id, action_type, source_system,
            source_customer_id, golden_id, performed_by, reason
        )
        SELECT
            action_id, review_id, action_type, source_system,
            source_customer_id, golden_id, performed_by, reason
        FROM read_csv_auto(?)
        WHERE action_id NOT IN (
            SELECT action_id FROM cdp.identity_resolution_action
        )
        """,
        [str(ACTIONS_CSV)],
    )

    after = conn.execute(
        "SELECT COUNT(*) FROM cdp.identity_resolution_action"
    ).fetchone()[0]

    print(f"Imported {after - before} action(s); {after} total in the audit trail.")
    if after > before:
        print("Re-run the clustering chain to apply them:")
        print("    python main.py --from identity")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--list", action="store_true", help="list OPEN reviews")
    parser.add_argument(
        "--import-actions",
        action="store_true",
        help=f"load steward decisions from {ACTIONS_CSV.name}",
    )
    parser.add_argument("--list-resolved", action="store_true", help="list RESOLVED reviews")
    parser.add_argument(
        "--restore-from-audit",
        action="store_true",
        help="recreate review rows for audit actions that lost theirs",
    )

    parser.add_argument("--review-id", help="the review to resolve")
    parser.add_argument("--source", help="alternative selector, e.g. MOBILE:MOB002")

    decision = parser.add_mutually_exclusive_group()
    decision.add_argument("--approve", action="store_true")
    decision.add_argument("--reject", action="store_true")

    parser.add_argument("--golden-id", help="target Golden Entity (required with --approve)")
    parser.add_argument("--by", default=DEFAULT_ACTOR, help=f"actor (default {DEFAULT_ACTOR})")
    parser.add_argument("--reason", help="why this decision was made")

    args = parser.parse_args()

    conn = duckdb.connect(str(DB_PATH))
    try:
        if args.list:
            list_reviews(conn, "OPEN")
            return 0
        if args.list_resolved:
            list_reviews(conn, "RESOLVED")
            return 0
        if args.import_actions:
            import_actions(conn)
            return 0
        if args.restore_from_audit:
            restore_from_audit(conn)
            return 0

        if not (args.approve or args.reject):
            parser.error("choose --approve or --reject (or use --list)")
        if not (args.review_id or args.source):
            parser.error("identify the review with --review-id or --source")
        if not args.reason:
            parser.error("--reason is required so the audit trail explains itself")

        resolve(conn, args)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
