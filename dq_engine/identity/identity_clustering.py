import hashlib
from pathlib import Path

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "database" / "cdp.duckdb"


def make_key(source_system, source_customer_id):
    return f"{source_system}::{source_customer_id}"


def cluster_signature(members) -> str:
    """Stable fingerprint of a cluster, from its sorted member keys.

    Order-independent, so the same set of members always produces the
    same signature no matter how clustering happened to walk them.
    """
    return hashlib.sha256("|".join(sorted(members)).encode("utf-8")).hexdigest()


def next_golden_id(conn) -> str:
    """Allocate the next unused golden id.

    Numbers are taken from the registry including retired rows, so a
    number is never handed out twice even after a cluster disappears.
    """
    row = conn.execute(
        """
        SELECT MAX(CAST(SUBSTR(golden_id, 2) AS INTEGER))
        FROM cdp.golden_entity_identity
        WHERE regexp_matches(golden_id, '^G[0-9]+$')
        """
    ).fetchone()

    highest = row[0] if row and row[0] is not None else 0
    return f"G{highest + 1:06d}"


def bootstrap_registry(conn) -> int:
    """Seed the registry from golden entities that predate it.

    Backfilled here rather than in SQL so the signature is produced by
    exactly the same code path that will later look it up - a separate
    SQL hash implementation could drift and silently mint new ids for
    clusters that already have one.

    Runs only when the registry is empty. Safe to call every time.
    """
    already = conn.execute(
        "SELECT COUNT(*) FROM cdp.golden_entity_identity"
    ).fetchone()[0]

    if already:
        return 0

    existing = conn.execute(
        """
        SELECT
            e.golden_id,
            m.source_system,
            m.source_customer_id
        FROM cdp.golden_entity e
        JOIN cdp.golden_entity_member m
          ON m.golden_id = e.golden_id
        """
    ).fetchall()

    if not existing:
        return 0

    members_by_golden: dict[str, set[str]] = {}
    for golden_id, source_system, source_customer_id in existing:
        members_by_golden.setdefault(golden_id, set()).add(
            make_key(source_system, source_customer_id)
        )

    for golden_id, members in sorted(members_by_golden.items()):
        conn.execute(
            """
            INSERT INTO cdp.golden_entity_identity (
                golden_id, cluster_signature, is_active
            )
            VALUES (?, ?, TRUE)
            ON CONFLICT (golden_id) DO NOTHING
            """,
            [golden_id, cluster_signature(members)],
        )

    return len(members_by_golden)


def resolve_golden_id(conn, members, claimed: set[str]) -> tuple[str, str]:
    """Find the golden id for this cluster, or mint one.

    Three steps, in order:

    1. Exact signature match - an unchanged cluster keeps its id. This is
       what makes a plain rebuild idempotent.
    2. Greatest member overlap with an active entity - a cluster that
       gained or lost members carries its id forward. Without this a
       steward approving MOB002 into G000002 would change the signature
       and mint a brand new id, orphaning the very approval that caused
       the change.
    3. Otherwise it is genuinely new, so allocate the next number.

    Returns (golden_id, reason) where reason is one of
    'signature' | 'overlap' | 'new'.
    """
    signature = cluster_signature(members)

    row = conn.execute(
        """
        SELECT golden_id
        FROM cdp.golden_entity_identity
        WHERE cluster_signature = ?
        ORDER BY golden_id
        LIMIT 1
        """,
        [signature],
    ).fetchone()

    if row and row[0] not in claimed:
        return row[0], "signature"

    # Overlap against the membership recorded for the previous run.
    candidates = conn.execute(
        """
        SELECT
            m.golden_id,
            m.source_system,
            m.source_customer_id
        FROM cdp.golden_entity_member m
        JOIN cdp.golden_entity_identity i
          ON i.golden_id = m.golden_id
        WHERE i.is_active = TRUE
        """
    ).fetchall()

    overlap: dict[str, int] = {}
    member_set = set(members)

    for golden_id, source_system, source_customer_id in candidates:
        if golden_id in claimed:
            continue
        if make_key(source_system, source_customer_id) in member_set:
            overlap[golden_id] = overlap.get(golden_id, 0) + 1

    if overlap:
        # Most shared members wins; golden_id breaks ties so the choice
        # does not depend on row order.
        best = sorted(overlap.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        return best, "overlap"

    return next_golden_id(conn), "new"


class UnionFind:
    def __init__(self, items):
        self.parent = {
            item: item
            for item in items
        }

    def find(self, item):
        if self.parent[item] != item:
            self.parent[item] = self.find(
                self.parent[item]
            )

        return self.parent[item]

    def union(self, left, right):
        left_root = self.find(left)
        right_root = self.find(right)

        if left_root != right_root:
            self.parent[right_root] = left_root


def main():
    conn = duckdb.connect(str(DB_PATH))

    # ============================================================
    # 1. Load all source records
    # ============================================================

    source_records = conn.execute("""
        SELECT
            source_system,
            source_customer_id
        FROM main.customer_unified
        ORDER BY source_system, source_customer_id
    """).fetchall()

    keys = [
        make_key(
            source_system,
            source_customer_id,
        )
        for source_system, source_customer_id
        in source_records
    ]

    uf = UnionFind(keys)

    # ============================================================
    # 2. Trusted MATCH edges
    # ============================================================

    trusted_edges = conn.execute("""
        SELECT
            source_system,
            source_customer_id,
            candidate_source_system,
            candidate_source_customer_id,
            match_score
        FROM cdp.identity_match
        WHERE match_status = 'MATCH'
          AND match_score >= 150
        ORDER BY match_score DESC
    """).fetchall()

    for (
        source_system,
        source_customer_id,
        candidate_source_system,
        candidate_source_customer_id,
        match_score,
    ) in trusted_edges:

        left = make_key(
            source_system,
            source_customer_id,
        )

        right = make_key(
            candidate_source_system,
            candidate_source_customer_id,
        )

        uf.union(left, right)

    # ============================================================
    # Approved manual resolutions
    # ============================================================

    approved_actions = conn.execute("""
        SELECT
            source_system,
            source_customer_id,
            golden_id
        FROM cdp.identity_resolution_action
        WHERE action_type = 'APPROVED'
    """).fetchall()

    golden_anchor = {}

    for (
        source_system,
        source_customer_id,
        golden_id,
    ) in approved_actions:

        source_key = make_key(
            source_system,
            source_customer_id,
        )

        if golden_id in golden_anchor:
            uf.union(
                source_key,
                golden_anchor[golden_id],
            )
        else:
            golden_anchor[golden_id] = source_key
    # ============================================================
    # 3. Build trusted clusters
    # ============================================================

    clusters = {}

    for key in keys:
        root = uf.find(key)

        clusters.setdefault(
            root,
            set(),
        ).add(key)

    # ============================================================
    # 4. Identify conflict records
    # ============================================================

    conflict_records = set()

    conflict_pairs = []

    conflicts = conn.execute("""
        SELECT
            source_system,
            source_customer_id,
            candidate_source_system,
            candidate_source_customer_id
        FROM cdp.identity_match
        WHERE match_status IN (
            'CONFLICT',
            'MATCH_WITH_CONFLICT'
        )
    """).fetchall()

    for (
        source_system,
        source_customer_id,
        candidate_source_system,
        candidate_source_customer_id,
    ) in conflicts:

        left = make_key(
            source_system,
            source_customer_id,
        )

        right = make_key(
            candidate_source_system,
            candidate_source_customer_id,
        )

        conflict_records.add(left)
        conflict_records.add(right)

        conflict_pairs.append(
            (left, right)
        )

    # ============================================================
    # 5. Attach conflict-only records to existing clusters
    #
    # Example:
    #
    # CRM003 ───── MOB003
    #    │
    #    │ conflict
    #    ▼
    #  LOS003
    #
    # LOS003 belongs to the REVIEW cluster but does not create
    # another standalone golden entity.
    # ============================================================

    cluster_for_member = {}

    for root, members in clusters.items():
        for member in members:
            cluster_for_member[member] = root

    assigned_conflict_members = set()

    for left, right in conflict_pairs:

        left_root = cluster_for_member.get(left)
        right_root = cluster_for_member.get(right)

        left_cluster_size = (
            len(clusters[left_root])
            if left_root is not None
            else 0
        )

        right_cluster_size = (
            len(clusters[right_root])
            if right_root is not None
            else 0
        )

        # Left is a real trusted cluster, right is singleton.
        if (
            left_root is not None
            and left_cluster_size >= 2
            and right_cluster_size == 1
        ):
            clusters[left_root].add(right)

            if right_root != left_root:
                clusters[right_root].discard(right)

            cluster_for_member[right] = left_root
            assigned_conflict_members.add(right)

        # Right is a real trusted cluster, left is singleton.
        elif (
            right_root is not None
            and right_cluster_size >= 2
            and left_cluster_size == 1
        ):
            clusters[right_root].add(left)

            if left_root != right_root:
                clusters[left_root].discard(left)

            cluster_for_member[left] = right_root
            assigned_conflict_members.add(left)

    # ============================================================
    # 6. Remove empty clusters
    # ============================================================

    clusters = {
        root: members
        for root, members in clusters.items()
        if members
    }

    # ============================================================
    # 7. Rebuild Golden Entity tables
    #
    # IMPORTANT:
    # Only clusters with >= 2 members become Golden Entities.
    # Singleton records remain unresolved.
    # ============================================================

    bootstrapped = bootstrap_registry(conn)
    if bootstrapped:
        print(f"Registry bootstrapped from {bootstrapped} existing entity(ies).")

    # Resolve ids BEFORE clearing the tables: the overlap rule compares
    # this run's clusters against the membership the previous run left
    # behind, so it has to read them while they are still there.
    resolved: list[tuple[str, list[str], bool]] = []
    claimed: set[str] = set()

    for root, members in sorted(
        clusters.items(),
        key=lambda item: sorted(item[1])
    ):

        members = sorted(members)

        # Singleton = unresolved, not Golden Entity.
        if len(members) < 2:
            continue

        has_conflict = any(
            member in conflict_records
            for member in members
        )

        golden_id, reason = resolve_golden_id(conn, members, claimed)
        claimed.add(golden_id)
        resolved.append((golden_id, members, has_conflict))

        if reason != "signature":
            print(f"  {golden_id}: matched by {reason} ({len(members)} members)")

    conn.execute("""
        DELETE FROM cdp.golden_entity_member
    """)

    conn.execute("""
        DELETE FROM cdp.golden_entity
    """)

    # Retire registry rows whose cluster no longer exists. Kept, never
    # deleted, so their numbers stay spent and history is preserved.
    if claimed:
        placeholders = ", ".join("?" for _ in claimed)
        conn.execute(
            f"""
            UPDATE cdp.golden_entity_identity
            SET is_active = FALSE, updated_at = CURRENT_TIMESTAMP
            WHERE is_active = TRUE AND golden_id NOT IN ({placeholders})
            """,
            list(claimed),
        )

    for golden_id, members, has_conflict in resolved:

        if has_conflict:
            entity_status = "REVIEW"
            confidence = "MEDIUM"
        else:
            entity_status = "ACTIVE"
            confidence = "HIGH"

        conn.execute(
            """
            INSERT INTO cdp.golden_entity_identity (
                golden_id, cluster_signature, is_active
            )
            VALUES (?, ?, TRUE)
            ON CONFLICT (golden_id) DO UPDATE SET
                cluster_signature = EXCLUDED.cluster_signature,
                updated_at = now(),
                is_active = TRUE
            """,
            [golden_id, cluster_signature(members)],
        )

        conn.execute(
            """
            INSERT INTO cdp.golden_entity (
                golden_id,
                entity_type,
                entity_status,
                has_conflict,
                confidence
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                golden_id,
                "CUSTOMER",
                entity_status,
                has_conflict,
                confidence,
            ],
        )

        for member in members:

            source_system, source_customer_id = (
                member.split("::", 1)
            )

            if has_conflict:
                membership_status = "REVIEW"
                membership_confidence = "MEDIUM"
            else:
                membership_status = "CONFIRMED"
                membership_confidence = "HIGH"

            conn.execute(
                """
                INSERT INTO cdp.golden_entity_member (
                    golden_id,
                    source_system,
                    source_customer_id,
                    membership_status,
                    membership_confidence
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    golden_id,
                    source_system,
                    source_customer_id,
                    membership_status,
                    membership_confidence,
                ],
            )

    # ============================================================
    # 8. Output
    # ============================================================

    print("\n=== GOLDEN ENTITY ===")

    print(
        conn.sql("""
            SELECT
                golden_id,
                entity_type,
                entity_status,
                has_conflict,
                confidence
            FROM cdp.golden_entity
            ORDER BY golden_id
        """)
    )

    print("\n=== GOLDEN ENTITY MEMBERS ===")

    print(
        conn.sql("""
            SELECT
                golden_id,
                source_system,
                source_customer_id,
                membership_status,
                membership_confidence
            FROM cdp.golden_entity_member
            ORDER BY golden_id, source_system
        """)
    )

    print("\n=== GOLDEN ID REGISTRY ===")

    print(
        conn.sql("""
            SELECT
                golden_id,
                SUBSTR(cluster_signature, 1, 12) AS signature,
                is_active,
                created_at,
                updated_at
            FROM cdp.golden_entity_identity
            ORDER BY golden_id
        """)
    )

    print("\n=== UNRESOLVED SOURCE RECORDS ===")

    print(
        conn.sql("""
            SELECT
                u.source_system,
                u.source_customer_id
            FROM (
                SELECT
                    source_system,
                    source_customer_id
                FROM main.customer_unified
            ) u

            LEFT JOIN cdp.golden_entity_member g
                ON u.source_system = g.source_system
               AND u.source_customer_id = g.source_customer_id

            WHERE g.source_customer_id IS NULL

            ORDER BY
                u.source_system,
                u.source_customer_id
        """)
    )

    conn.close()


if __name__ == "__main__":
    main()