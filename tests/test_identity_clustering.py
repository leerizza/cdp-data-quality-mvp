"""Phase 1 - persistent golden ids.

The property under test: rebuilding clusters must never renumber an
entity that already exists, because cdp.identity_resolution_action and
every downstream table reference the golden_id.
"""

from __future__ import annotations

from conftest import add_member


# ---------------------------------------------------------------- signature


def test_signature_is_order_independent(clustering):
    a = clustering.cluster_signature(["CRM::CRM001", "LOS::LOS001", "MOBILE::MOB001"])
    b = clustering.cluster_signature(["MOBILE::MOB001", "CRM::CRM001", "LOS::LOS001"])
    assert a == b


def test_signature_changes_with_membership(clustering):
    two = clustering.cluster_signature(["CRM::CRM002", "LOS::LOS002"])
    three = clustering.cluster_signature(["CRM::CRM002", "LOS::LOS002", "MOBILE::MOB002"])
    assert two != three


# ---------------------------------------------------------------- allocation


def test_first_id_is_g000001(clustering, conn):
    clustering.DB_PATH = None  # unused: helpers take an explicit connection
    assert clustering.next_golden_id(conn) == "G000001"


def test_ids_do_not_reuse_retired_numbers(clustering, conn):
    conn.execute(
        """
        INSERT INTO cdp.golden_entity_identity (golden_id, cluster_signature, is_active)
        VALUES ('G000001', 'sig-a', TRUE), ('G000002', 'sig-b', FALSE)
        """
    )
    # G000002 is retired, so the next number must still be 3.
    assert clustering.next_golden_id(conn) == "G000003"


# ---------------------------------------------------------------- resolution


def test_unchanged_cluster_keeps_its_id_by_signature(clustering, conn):
    members = ["CRM::CRM001", "LOS::LOS001", "MOBILE::MOB001"]
    conn.execute(
        "INSERT INTO cdp.golden_entity_identity (golden_id, cluster_signature) VALUES (?, ?)",
        ["G000001", clustering.cluster_signature(members)],
    )

    golden_id, reason = clustering.resolve_golden_id(conn, members, set())

    assert golden_id == "G000001"
    assert reason == "signature"


def test_cluster_that_gained_a_member_keeps_its_id_by_overlap(clustering, conn):
    """The MOB002 case: approving a new member must not mint a new id."""
    before = ["CRM::CRM002", "LOS::LOS002"]
    conn.execute(
        "INSERT INTO cdp.golden_entity_identity (golden_id, cluster_signature) VALUES (?, ?)",
        ["G000002", clustering.cluster_signature(before)],
    )
    add_member(conn, "G000002", "CRM", "CRM002")
    add_member(conn, "G000002", "LOS", "LOS002")

    after = ["CRM::CRM002", "LOS::LOS002", "MOBILE::MOB002"]
    golden_id, reason = clustering.resolve_golden_id(conn, after, set())

    assert golden_id == "G000002"
    assert reason == "overlap"


def test_genuinely_new_cluster_gets_a_new_id(clustering, conn):
    conn.execute(
        "INSERT INTO cdp.golden_entity_identity (golden_id, cluster_signature) VALUES (?, ?)",
        ["G000001", clustering.cluster_signature(["CRM::CRM001", "LOS::LOS001"])],
    )
    add_member(conn, "G000001", "CRM", "CRM001")
    add_member(conn, "G000001", "LOS", "LOS001")

    golden_id, reason = clustering.resolve_golden_id(
        conn, ["CRM::CRM009", "LOS::LOS009"], set()
    )

    assert golden_id == "G000002"
    assert reason == "new"


def test_an_id_is_not_handed_to_two_clusters_in_one_run(clustering, conn):
    """A split must not give both halves the same id."""
    members = ["CRM::CRM001", "LOS::LOS001"]
    conn.execute(
        "INSERT INTO cdp.golden_entity_identity (golden_id, cluster_signature) VALUES (?, ?)",
        ["G000001", clustering.cluster_signature(members)],
    )
    add_member(conn, "G000001", "CRM", "CRM001")
    add_member(conn, "G000001", "LOS", "LOS001")

    first, _ = clustering.resolve_golden_id(conn, members, set())
    second, _ = clustering.resolve_golden_id(conn, members, {first})

    assert first == "G000001"
    assert second != first


# ---------------------------------------------------------------- bootstrap


def test_bootstrap_adopts_pre_registry_entities(clustering, conn):
    """Entities that existed before the registry keep their ids."""
    add_member(conn, "G000001", "CRM", "CRM001")
    add_member(conn, "G000001", "LOS", "LOS001")
    add_member(conn, "G000002", "CRM", "CRM002")
    add_member(conn, "G000002", "LOS", "LOS002")

    assert clustering.bootstrap_registry(conn) == 2

    rows = conn.execute(
        "SELECT golden_id FROM cdp.golden_entity_identity ORDER BY golden_id"
    ).fetchall()
    assert [r[0] for r in rows] == ["G000001", "G000002"]

    # The adopted signature must match what resolve_golden_id will look up.
    golden_id, reason = clustering.resolve_golden_id(
        conn, ["CRM::CRM001", "LOS::LOS001"], set()
    )
    assert (golden_id, reason) == ("G000001", "signature")


def test_bootstrap_is_idempotent(clustering, conn):
    add_member(conn, "G000001", "CRM", "CRM001")
    add_member(conn, "G000001", "LOS", "LOS001")

    assert clustering.bootstrap_registry(conn) == 1
    assert clustering.bootstrap_registry(conn) == 0

    count = conn.execute("SELECT COUNT(*) FROM cdp.golden_entity_identity").fetchone()[0]
    assert count == 1
