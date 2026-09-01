"""Phase 6 - type 2 history for the golden layer.

The golden tables are rebuilt wholesale each run. These tests pin that
the previous profile survives that rebuild, and - just as important -
that an unchanged rebuild does not manufacture versions.
"""

from __future__ import annotations

import duckdb
import pytest

from conftest import golden_of, run_engine


@pytest.fixture
def historied(pipeline_db):
    run_engine("identity/identity_candidate_generator.py", pipeline_db)
    run_engine("identity/identity_decision.py", pipeline_db)
    run_engine("identity/identity_clustering.py", pipeline_db)
    run_engine("source_dq/attribute_dq.py", pipeline_db)
    run_engine("golden/survivorship_engine.py", pipeline_db)
    run_engine("golden/golden_history.py", pipeline_db)

    conn = duckdb.connect(str(pipeline_db))
    yield conn, pipeline_db
    conn.close()


def versions(conn, golden_id):
    return conn.execute(
        """
        SELECT full_name, is_current, change_reason, valid_from, valid_to
        FROM cdp.golden_customer_history
        WHERE golden_id = ? ORDER BY valid_from
        """,
        [golden_id],
    ).fetchall()


# ------------------------------------------------------------- first run


def test_first_run_opens_a_current_version_per_customer(historied):
    conn, _ = historied

    live = conn.execute("SELECT COUNT(*) FROM cdp.golden_customer").fetchone()[0]
    current = conn.execute(
        "SELECT COUNT(*) FROM cdp.golden_customer_history WHERE is_current = TRUE"
    ).fetchone()[0]

    assert live > 0
    assert current == live


def test_first_version_is_marked_initial(historied):
    conn, _ = historied
    reasons = conn.execute(
        "SELECT DISTINCT change_reason FROM cdp.golden_customer_history"
    ).fetchall()
    assert reasons == [("INITIAL",)]


def test_members_are_versioned_too(historied):
    conn, _ = historied
    live = conn.execute("SELECT COUNT(*) FROM cdp.golden_entity_member").fetchone()[0]
    current = conn.execute(
        "SELECT COUNT(*) FROM cdp.golden_entity_member_history WHERE is_current = TRUE"
    ).fetchone()[0]
    assert current == live


def test_open_versions_have_no_end_date(historied):
    conn, _ = historied
    dangling = conn.execute(
        """
        SELECT COUNT(*) FROM cdp.golden_customer_history
        WHERE is_current = TRUE AND valid_to IS NOT NULL
        """
    ).fetchone()[0]
    assert dangling == 0


# ----------------------------------------------------------- idempotency


def test_rerunning_unchanged_creates_no_new_version(historied):
    conn, pipeline_db = historied
    before = conn.execute(
        "SELECT COUNT(*) FROM cdp.golden_customer_history"
    ).fetchone()[0]
    conn.close()

    run_engine("golden/golden_history.py", pipeline_db)
    run_engine("golden/golden_history.py", pipeline_db)

    connection = duckdb.connect(str(pipeline_db))
    try:
        after = connection.execute(
            "SELECT COUNT(*) FROM cdp.golden_customer_history"
        ).fetchone()[0]
        assert after == before, "unchanged data must not manufacture versions"
    finally:
        connection.close()


def test_survivorship_rebuild_alone_does_not_version(historied):
    """Survivorship rewrites the table every run with the same values."""
    conn, pipeline_db = historied
    before = conn.execute(
        "SELECT COUNT(*) FROM cdp.golden_customer_history"
    ).fetchone()[0]
    conn.close()

    run_engine("golden/survivorship_engine.py", pipeline_db)
    run_engine("golden/golden_history.py", pipeline_db)

    connection = duckdb.connect(str(pipeline_db))
    try:
        after = connection.execute(
            "SELECT COUNT(*) FROM cdp.golden_customer_history"
        ).fetchone()[0]
        assert after == before
    finally:
        connection.close()


# --------------------------------------------------------- change tracking


def test_a_changed_value_closes_the_old_version_and_opens_a_new_one(historied):
    conn, pipeline_db = historied
    golden_id = conn.execute(
        "SELECT MIN(golden_id) FROM cdp.golden_customer"
    ).fetchone()[0]

    conn.execute(
        "UPDATE cdp.golden_customer SET full_name = 'Changed Name' WHERE golden_id = ?",
        [golden_id],
    )
    conn.close()

    run_engine("golden/golden_history.py", pipeline_db)

    connection = duckdb.connect(str(pipeline_db))
    try:
        rows = versions(connection, golden_id)

        assert len(rows) == 2, "the previous profile must survive"

        old, new = rows
        assert old[1] is False and old[4] is not None, "old version must be closed"
        assert new[0] == "Changed Name"
        assert new[1] is True and new[4] is None
        assert new[2] == "CHANGED:full_name"
    finally:
        connection.close()


def test_change_reason_names_every_field_that_moved(historied):
    conn, pipeline_db = historied
    golden_id = conn.execute(
        "SELECT MIN(golden_id) FROM cdp.golden_customer"
    ).fetchone()[0]

    conn.execute(
        """
        UPDATE cdp.golden_customer
        SET full_name = 'Another Name', phone = '080000000000'
        WHERE golden_id = ?
        """,
        [golden_id],
    )
    conn.close()

    run_engine("golden/golden_history.py", pipeline_db)

    connection = duckdb.connect(str(pipeline_db))
    try:
        reason = connection.execute(
            """
            SELECT change_reason FROM cdp.golden_customer_history
            WHERE golden_id = ? AND is_current = TRUE
            """,
            [golden_id],
        ).fetchone()[0]
        assert "full_name" in reason
        assert "phone" in reason
    finally:
        connection.close()


def test_a_removed_customer_is_closed_not_deleted(historied):
    """Never lose the previous profile, even if the entity goes away."""
    conn, pipeline_db = historied
    golden_id = conn.execute(
        "SELECT MIN(golden_id) FROM cdp.golden_customer"
    ).fetchone()[0]

    conn.execute("DELETE FROM cdp.golden_customer WHERE golden_id = ?", [golden_id])
    conn.close()

    run_engine("golden/golden_history.py", pipeline_db)

    connection = duckdb.connect(str(pipeline_db))
    try:
        rows = versions(connection, golden_id)
        assert len(rows) == 1, "the history row must still be there"
        assert rows[0][1] is False, "and must be closed"
        assert rows[0][4] is not None
    finally:
        connection.close()


def test_membership_change_is_versioned(historied):
    conn, pipeline_db = historied
    golden_id = golden_of(conn, "CRM001")

    conn.execute(
        """
        UPDATE cdp.golden_entity_member
        SET membership_status = 'REVIEW'
        WHERE golden_id = ? AND source_customer_id = 'CRM001'
        """,
        [golden_id],
    )
    conn.close()

    run_engine("golden/golden_history.py", pipeline_db)

    connection = duckdb.connect(str(pipeline_db))
    try:
        rows = connection.execute(
            """
            SELECT membership_status, is_current
            FROM cdp.golden_entity_member_history
            WHERE source_customer_id = 'CRM001' ORDER BY valid_from
            """
        ).fetchall()
        assert len(rows) == 2
        assert rows[0] == ("CONFIRMED", False)
        assert rows[1] == ("REVIEW", True)
    finally:
        connection.close()


def test_only_one_current_version_per_key(historied):
    conn, pipeline_db = historied
    conn.execute(
        "UPDATE cdp.golden_customer SET phone = '081111111111'"
    )
    conn.close()

    run_engine("golden/golden_history.py", pipeline_db)

    connection = duckdb.connect(str(pipeline_db))
    try:
        duplicated = connection.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT golden_id FROM cdp.golden_customer_history
                WHERE is_current = TRUE
                GROUP BY golden_id HAVING COUNT(*) > 1
            )
            """
        ).fetchone()[0]
        assert duplicated == 0
    finally:
        connection.close()
