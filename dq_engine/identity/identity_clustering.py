from pathlib import Path

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "database" / "cdp.duckdb"


def make_key(source_system, source_customer_id):
    return f"{source_system}::{source_customer_id}"


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

    conn.execute("""
        DELETE FROM cdp.golden_entity_member
    """)

    conn.execute("""
        DELETE FROM cdp.golden_entity
    """)

    golden_counter = 1

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

        if has_conflict:
            entity_status = "REVIEW"
            confidence = "MEDIUM"
        else:
            entity_status = "ACTIVE"
            confidence = "HIGH"

        golden_id = (
            f"G{golden_counter:06d}"
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

        golden_counter += 1

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