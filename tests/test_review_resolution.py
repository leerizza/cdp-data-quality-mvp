"""Phase 3 - the review and remediation loop.

The property that matters: a steward decision is an input to clustering,
not a note written next to it. Approving MOB002 must actually move it
into the entity, and rebuilding must not undo that.
"""

from __future__ import annotations

import duckdb
import pytest

from conftest import approve, golden_of, run_engine


def cluster(db_path):
    """Run the identity chain end to end."""
    run_engine("identity/identity_candidate_generator.py", db_path)
    run_engine("identity/identity_decision.py", db_path)
    run_engine("identity/identity_clustering.py", db_path)


@pytest.fixture
def clustered(pipeline_db):
    cluster(pipeline_db)
    conn = duckdb.connect(str(pipeline_db))
    yield conn
    conn.close()


# ---------------------------------------------------- before any approval


def test_mob002_is_unresolved_without_a_decision(clustered):
    """It is only a POSSIBLE_MATCH, which is not enough to join on its own."""
    assert golden_of(clustered, "MOB002") is None


def test_review_queue_raises_the_possible_match(pipeline_db):
    cluster(pipeline_db)
    run_engine("review/review_queue_builder.py", pipeline_db)

    conn = duckdb.connect(str(pipeline_db))
    rows = conn.execute(
        """
        SELECT COUNT(*) FROM cdp.review_queue
        WHERE issue_type = 'POSSIBLE_MATCH' AND status = 'OPEN'
        """
    ).fetchone()[0]
    conn.close()

    assert rows > 0


# ----------------------------------------------------- after the approval


def test_approved_mob002_joins_crm002s_entity(pipeline_db):
    """The required assertion: MOB002 approved -> joins G000002."""
    cluster(pipeline_db)

    conn = duckdb.connect(str(pipeline_db))
    target = golden_of(conn, "CRM002")
    assert target is not None, "CRM002 should already be in an entity"
    approve(conn, "MOBILE", "MOB002", target)
    conn.close()

    run_engine("identity/identity_clustering.py", pipeline_db)

    conn = duckdb.connect(str(pipeline_db))
    try:
        assert golden_of(conn, "MOB002") == target
        assert golden_of(conn, "CRM002") == target
        assert golden_of(conn, "LOS002") == target
    finally:
        conn.close()


def test_approval_keeps_the_entity_id_it_was_recorded_against(pipeline_db):
    """Phase 1 and Phase 3 together.

    The approval names a golden_id. If clustering minted a new id when
    membership changed, the approval would point at an entity that no
    longer exists.
    """
    cluster(pipeline_db)

    conn = duckdb.connect(str(pipeline_db))
    target = golden_of(conn, "CRM002")
    approve(conn, "MOBILE", "MOB002", target)
    conn.close()

    run_engine("identity/identity_clustering.py", pipeline_db)

    conn = duckdb.connect(str(pipeline_db))
    try:
        still_exists = conn.execute(
            "SELECT COUNT(*) FROM cdp.golden_entity WHERE golden_id = ?", [target]
        ).fetchone()[0]
        assert still_exists == 1, f"{target} was renumbered away from its approval"
    finally:
        conn.close()


def test_membership_is_stable_across_rebuilds(pipeline_db):
    cluster(pipeline_db)

    conn = duckdb.connect(str(pipeline_db))
    target = golden_of(conn, "CRM002")
    approve(conn, "MOBILE", "MOB002", target)
    conn.close()

    run_engine("identity/identity_clustering.py", pipeline_db)
    conn = duckdb.connect(str(pipeline_db))
    first = conn.execute(
        "SELECT golden_id, source_customer_id FROM cdp.golden_entity_member ORDER BY 1, 2"
    ).fetchall()
    conn.close()

    run_engine("identity/identity_clustering.py", pipeline_db)
    conn = duckdb.connect(str(pipeline_db))
    second = conn.execute(
        "SELECT golden_id, source_customer_id FROM cdp.golden_entity_member ORDER BY 1, 2"
    ).fetchall()
    conn.close()

    assert first == second


def test_rebuilding_the_queue_keeps_resolved_reviews(pipeline_db):
    """Resolved reviews are decisions, not derived rows."""
    cluster(pipeline_db)
    run_engine("review/review_queue_builder.py", pipeline_db)

    conn = duckdb.connect(str(pipeline_db))
    review_id = conn.execute(
        "SELECT review_id FROM cdp.review_queue WHERE status = 'OPEN' LIMIT 1"
    ).fetchone()[0]
    conn.execute(
        """
        UPDATE cdp.review_queue
        SET status = 'RESOLVED', resolution = 'APPROVED'
        WHERE review_id = ?
        """,
        [review_id],
    )
    conn.close()

    run_engine("review/review_queue_builder.py", pipeline_db)

    conn = duckdb.connect(str(pipeline_db))
    try:
        survived = conn.execute(
            "SELECT status FROM cdp.review_queue WHERE review_id = ?", [review_id]
        ).fetchone()
        assert survived is not None, "the resolved review was deleted by a rebuild"
        assert survived[0] == "RESOLVED"
    finally:
        conn.close()
