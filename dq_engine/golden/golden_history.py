"""Type 2 history for the golden layer.

cdp.golden_customer and cdp.golden_entity_member are rebuilt from scratch
on every run, so yesterday's profile is overwritten with nothing left
behind. That is fine for a current-state table and unacceptable for a
customer record someone acted on: "what did we believe about G000002 in
March, and why did it change?" has to be answerable.

This compares the live tables against the rows currently marked
is_current and, where they differ, closes the old version and opens a
new one with a reason.

Idempotent by construction: a row is only written when its tracked
values actually changed, so re-running over unchanged data records
nothing. Run it after survivorship, by which point clustering has
settled membership and survivorship has picked the surviving values.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "database" / "cdp.duckdb"


# --------------------------------------------------------------- helpers


def close_version(conn, table: str, history_id: str) -> None:
    conn.execute(
        f"""
        UPDATE {table}
        SET valid_to = now(), is_current = FALSE
        WHERE history_id = ?
        """,
        [history_id],
    )


# ------------------------------------------------------- golden customer

CUSTOMER_FIELDS = (
    "nik",
    "full_name",
    "phone",
    "email",
    "birth_date",
    "entity_status",
    "confidence",
)


def track_customers(conn) -> dict[str, int]:
    live = {
        row[0]: row[1:]
        for row in conn.execute(
            """
            SELECT golden_id, nik, full_name, phone, email, birth_date,
                   entity_status, confidence
            FROM cdp.golden_customer
            """
        ).fetchall()
    }

    current = {
        row[1]: (row[0], row[2:])
        for row in conn.execute(
            """
            SELECT history_id, golden_id, nik, full_name, phone, email,
                   birth_date, entity_status, confidence
            FROM cdp.golden_customer_history
            WHERE is_current = TRUE
            """
        ).fetchall()
    }

    stats = {"new": 0, "changed": 0, "removed": 0, "unchanged": 0}

    for golden_id, values in sorted(live.items()):

        existing = current.get(golden_id)

        if existing is None:
            insert_customer(conn, golden_id, values, "INITIAL")
            stats["new"] += 1
            continue

        history_id, previous = existing

        if previous == values:
            stats["unchanged"] += 1
            continue

        reason = describe_change(CUSTOMER_FIELDS, previous, values)
        close_version(conn, "cdp.golden_customer_history", history_id)
        insert_customer(conn, golden_id, values, reason)
        stats["changed"] += 1

    # Gone from the live table: close the version, keep the record.
    for golden_id, (history_id, _) in sorted(current.items()):
        if golden_id not in live:
            close_version(conn, "cdp.golden_customer_history", history_id)
            stats["removed"] += 1

    return stats


def insert_customer(conn, golden_id: str, values, reason: str) -> None:
    conn.execute(
        """
        INSERT INTO cdp.golden_customer_history (
            history_id, golden_id, nik, full_name, phone, email, birth_date,
            entity_status, confidence, valid_from, is_current, change_reason
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, now(), TRUE, ?)
        """,
        [str(uuid4()), golden_id, *values, reason],
    )


def describe_change(fields, previous, current) -> str:
    """Name the attributes that moved, so history explains itself."""
    changed = [
        field
        for field, before, after in zip(fields, previous, current)
        if before != after
    ]
    return "CHANGED:" + ",".join(changed) if changed else "CHANGED"


# --------------------------------------------------------- entity member

MEMBER_FIELDS = ("membership_status", "membership_confidence")


def track_members(conn) -> dict[str, int]:
    live = {
        (row[0], row[1], row[2]): (row[3], row[4])
        for row in conn.execute(
            """
            SELECT golden_id, source_system, source_customer_id,
                   membership_status, membership_confidence
            FROM cdp.golden_entity_member
            """
        ).fetchall()
    }

    current = {
        (row[1], row[2], row[3]): (row[0], (row[4], row[5]))
        for row in conn.execute(
            """
            SELECT history_id, golden_id, source_system, source_customer_id,
                   membership_status, membership_confidence
            FROM cdp.golden_entity_member_history
            WHERE is_current = TRUE
            """
        ).fetchall()
    }

    stats = {"new": 0, "changed": 0, "removed": 0, "unchanged": 0}

    for key, values in sorted(live.items()):

        existing = current.get(key)

        if existing is None:
            insert_member(conn, key, values, "MEMBER_ADDED")
            stats["new"] += 1
            continue

        history_id, previous = existing

        if previous == values:
            stats["unchanged"] += 1
            continue

        reason = describe_change(MEMBER_FIELDS, previous, values)
        close_version(conn, "cdp.golden_entity_member_history", history_id)
        insert_member(conn, key, values, reason)
        stats["changed"] += 1

    for key, (history_id, _) in sorted(current.items()):
        if key not in live:
            close_version(conn, "cdp.golden_entity_member_history", history_id)
            stats["removed"] += 1

    return stats


def insert_member(conn, key, values, reason: str) -> None:
    golden_id, source_system, source_customer_id = key
    conn.execute(
        """
        INSERT INTO cdp.golden_entity_member_history (
            history_id, golden_id, source_system, source_customer_id,
            membership_status, membership_confidence,
            valid_from, is_current, change_reason
        )
        VALUES (?, ?, ?, ?, ?, ?, now(), TRUE, ?)
        """,
        [str(uuid4()), golden_id, source_system, source_customer_id, *values, reason],
    )


# ------------------------------------------------------------------ main


def main() -> int:
    conn = duckdb.connect(str(DB_PATH))

    customers = track_customers(conn)
    members = track_members(conn)

    print("\n=== GOLDEN HISTORY ===")
    print(
        f"golden_customer : {customers['new']} new, {customers['changed']} changed, "
        f"{customers['removed']} closed, {customers['unchanged']} unchanged"
    )
    print(
        f"members         : {members['new']} new, {members['changed']} changed, "
        f"{members['removed']} closed, {members['unchanged']} unchanged"
    )

    print("\n=== CURRENT GOLDEN VERSIONS ===")
    print(
        conn.sql(
            """
            SELECT
                golden_id,
                nik,
                full_name,
                entity_status,
                valid_from,
                change_reason
            FROM cdp.golden_customer_history
            WHERE is_current = TRUE
            ORDER BY golden_id
            """
        )
    )

    superseded = conn.execute(
        "SELECT COUNT(*) FROM cdp.golden_customer_history WHERE is_current = FALSE"
    ).fetchone()[0]

    if superseded:
        print("\n=== SUPERSEDED VERSIONS ===")
        print(
            conn.sql(
                """
                SELECT
                    golden_id,
                    full_name,
                    change_reason,
                    valid_from,
                    valid_to
                FROM cdp.golden_customer_history
                WHERE is_current = FALSE
                ORDER BY golden_id, valid_from
                """
            )
        )

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
