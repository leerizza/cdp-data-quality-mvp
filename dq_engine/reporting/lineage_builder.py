"""Golden record lineage - explain where every surviving value came from.

Each golden attribute is joined back through the chain that produced it:

    golden attribute
      <- surviving source record  (survivorship + source priority)
      <- attribute-level DQ verdict
      <- entity membership        (identity clustering)
      <- steward resolution       (if the membership was human-approved)
      <- golden entity status

so "why is G000002.phone 085273063633?" has an answer on one row.

Usage:
    python dq_engine/lineage_builder.py                  # rebuild the table
    python dq_engine/lineage_builder.py --explain G000002
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "database" / "cdp.duckdb"


def build(conn) -> int:
    run_id = conn.execute(
        "SELECT run_id FROM dq.dq_run ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    run_id = run_id[0] if run_id else None

    conn.execute("DELETE FROM cdp.golden_lineage")

    # The attribute DQ verdict is per run, so pick the newest verdict for
    # each source record / attribute pair rather than joining them all.
    conn.execute(
        """
        INSERT INTO cdp.golden_lineage (
            golden_id, attribute_name, attribute_value,
            source_system, source_customer_id, source_priority,
            dq_eligible, attribute_dq_status, attribute_dq_message,
            membership_status, membership_confidence,
            resolution_action, resolved_by,
            entity_status, entity_confidence,
            selection_reason, run_id
        )
        WITH latest_attr_dq AS (
            SELECT
                source_system,
                source_customer_id,
                attribute_name,
                status,
                message,
                ROW_NUMBER() OVER (
                    PARTITION BY source_system, source_customer_id, attribute_name
                    ORDER BY created_at DESC
                ) AS rn
            FROM dq.dq_attribute_result
        ),
        latest_action AS (
            SELECT
                source_system,
                source_customer_id,
                golden_id,
                action_type,
                performed_by,
                ROW_NUMBER() OVER (
                    PARTITION BY source_system, source_customer_id
                    ORDER BY created_at DESC
                ) AS rn
            FROM cdp.identity_resolution_action
        )
        SELECT
            a.golden_id,
            a.attribute_name,
            a.attribute_value,
            a.source_system,
            a.source_customer_id,
            a.source_priority,
            a.dq_eligible,
            d.status,
            d.message,
            m.membership_status,
            m.membership_confidence,
            act.action_type,
            act.performed_by,
            e.entity_status,
            e.confidence,
            a.selection_reason,
            ?
        FROM cdp.golden_customer_attribute a

        LEFT JOIN latest_attr_dq d
            ON d.rn = 1
           AND d.source_system = a.source_system
           AND d.source_customer_id = a.source_customer_id
           AND d.attribute_name = a.attribute_name

        LEFT JOIN cdp.golden_entity_member m
            ON m.golden_id = a.golden_id
           AND m.source_system = a.source_system
           AND m.source_customer_id = a.source_customer_id

        LEFT JOIN latest_action act
            ON act.rn = 1
           AND act.source_system = a.source_system
           AND act.source_customer_id = a.source_customer_id
           AND act.golden_id = a.golden_id

        LEFT JOIN cdp.golden_entity e
            ON e.golden_id = a.golden_id
        """,
        [run_id],
    )

    return conn.execute("SELECT COUNT(*) FROM cdp.golden_lineage").fetchone()[0]


def explain(conn, golden_id: str) -> None:
    entity = conn.execute(
        """
        SELECT entity_status, confidence, has_conflict
        FROM cdp.golden_entity WHERE golden_id = ?
        """,
        [golden_id],
    ).fetchone()

    if entity is None:
        raise SystemExit(f"{golden_id} is not a known Golden Entity.")

    print(f"\n=== LINEAGE: {golden_id} ===")
    print(f"entity status : {entity[0]}  confidence={entity[1]}  conflict={entity[2]}")

    members = conn.execute(
        """
        SELECT source_system, source_customer_id, membership_status, membership_confidence
        FROM cdp.golden_entity_member
        WHERE golden_id = ?
        ORDER BY source_system
        """,
        [golden_id],
    ).fetchall()

    print("\nmembers:")
    for m in members:
        print(f"  {m[0]}:{m[1]:<10} {m[2]} ({m[3]})")

    actions = conn.execute(
        """
        SELECT source_system, source_customer_id, action_type, performed_by, reason
        FROM cdp.identity_resolution_action
        WHERE golden_id = ?
        ORDER BY created_at
        """,
        [golden_id],
    ).fetchall()

    if actions:
        print("\nsteward decisions:")
        for a in actions:
            print(f"  {a[0]}:{a[1]} {a[2]} by {a[3]}")
            print(f"      {a[4]}")

    rows = conn.execute(
        """
        SELECT
            attribute_name, attribute_value, source_system, source_customer_id,
            source_priority, dq_eligible, attribute_dq_status, attribute_dq_message,
            resolution_action
        FROM cdp.golden_lineage
        WHERE golden_id = ?
        ORDER BY attribute_name
        """,
        [golden_id],
    ).fetchall()

    if not rows:
        print("\nno surviving attributes (entity not processed by survivorship)")
        return

    print("\nsurviving attributes:")
    for r in rows:
        via = " via steward approval" if r[8] == "APPROVED" else ""
        print(f"\n  {r[0]} = {r[1]}")
        print(f"      from      : {r[2]}:{r[3]} (priority {r[4]}){via}")
        print(f"      attr DQ   : {r[6]} - {r[7]}")
        print(f"      eligible  : {r[5]}")

    # Show what lost, so the decision is visible from both sides.
    rejected = conn.execute(
        """
        SELECT d.attribute_name, d.source_system, d.source_customer_id, d.status, d.message
        FROM dq.dq_attribute_result d
        JOIN cdp.golden_entity_member m
          ON m.source_system = d.source_system
         AND m.source_customer_id = d.source_customer_id
        WHERE m.golden_id = ?
          AND d.status = 'FAIL'
        GROUP BY ALL
        ORDER BY d.attribute_name, d.source_system
        """,
        [golden_id],
    ).fetchall()

    if rejected:
        print("\nrejected by attribute DQ (could not survive):")
        for r in rejected:
            print(f"  {r[1]}:{r[2]} {r[0]} -> {r[3]} ({r[4]})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build or query golden record lineage.")
    parser.add_argument("--explain", metavar="GOLDEN_ID", help="print one entity's lineage")
    args = parser.parse_args()

    conn = duckdb.connect(str(DB_PATH))
    try:
        if args.explain:
            explain(conn, args.explain)
            return 0

        count = build(conn)
        print(f"\n=== GOLDEN LINEAGE ===")
        print(f"built {count} attribute lineage row(s)")
        print(
            conn.sql(
                """
                SELECT
                    golden_id, attribute_name, attribute_value,
                    source_system || ':' || source_customer_id AS source,
                    attribute_dq_status AS attr_dq,
                    resolution_action AS steward
                FROM cdp.golden_lineage
                ORDER BY golden_id, attribute_name
                """
            )
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
