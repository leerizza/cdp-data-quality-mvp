"""Phase 3 - golden quality and the CDP score.

Two properties matter here:

  * an entity awaiting review is NOT_ASSESSED, not zero. Scoring it zero
    reports "14% quality" when the truth is "not scored yet", and drags
    the CDP average down with a number that means nothing.
  * CRITICAL severity is zero tolerance at the gate, whatever the score.
"""

from __future__ import annotations

import duckdb
import pytest

from conftest import golden_of, run_engine


@pytest.fixture
def scored(pipeline_db):
    """Run the chain through to the golden quality score."""
    run_engine("identity/identity_candidate_generator.py", pipeline_db)
    run_engine("identity/identity_decision.py", pipeline_db)
    run_engine("identity/identity_clustering.py", pipeline_db)
    run_engine("source_dq/attribute_dq.py", pipeline_db)
    run_engine("golden/survivorship_engine.py", pipeline_db)
    run_engine("golden/golden_quality_score.py", pipeline_db)

    conn = duckdb.connect(str(pipeline_db))
    yield conn, pipeline_db
    conn.close()


def score_row(conn, golden_id):
    return conn.execute(
        """
        SELECT quality_status, quality_score, completeness_score,
               validity_score, has_conflict
        FROM cdp.golden_quality_score WHERE golden_id = ?
        """,
        [golden_id],
    ).fetchone()


# ------------------------------------------------------- golden quality


def test_every_entity_is_scored_or_explicitly_not_assessed(scored):
    conn, _ = scored
    rows = conn.execute(
        "SELECT golden_id, quality_status FROM cdp.golden_quality_score"
    ).fetchall()

    assert rows
    for golden_id, status in rows:
        assert status in {
            "EXCELLENT", "GOOD", "WARNING", "POOR", "REVIEW", "NOT_ASSESSED"
        }, f"{golden_id} has status {status}"


def test_clean_entity_scores_full_marks(scored):
    """CRM001/LOS001/MOB001 agree on everything and pass DQ."""
    conn, _ = scored
    golden_id = golden_of(conn, "CRM001")
    row = score_row(conn, golden_id)

    assert row is not None
    assert row[0] == "EXCELLENT"
    assert row[1] == 100.0


def test_conflicted_entity_is_not_assessed_rather_than_zero(scored):
    """CRM003's entity is in REVIEW, so it never reached survivorship."""
    conn, _ = scored
    golden_id = golden_of(conn, "CRM003")
    row = score_row(conn, golden_id)

    assert row is not None
    assert row[0] == "NOT_ASSESSED"
    assert row[1] is None, "an unreviewed entity must not carry a numeric score"
    assert row[4] is True


def test_conflicted_entity_is_flagged_for_review(scored):
    """The required assertion: CRM003 conflict -> REVIEW."""
    conn, _ = scored
    golden_id = golden_of(conn, "CRM003")

    status = conn.execute(
        "SELECT entity_status FROM cdp.golden_entity WHERE golden_id = ?",
        [golden_id],
    ).fetchone()[0]

    assert status == "REVIEW"


def test_all_three_003_records_share_one_entity(scored):
    """A conflict groups the records for review, it does not split them."""
    conn, _ = scored
    assert golden_of(conn, "CRM003") == golden_of(conn, "LOS003")
    assert golden_of(conn, "CRM003") == golden_of(conn, "MOB003")


# ------------------------------------------------------------ CDP score


@pytest.fixture
def cdp_scored(scored):
    conn, pipeline_db = scored

    # The CDP score is keyed on a DQ run.
    conn.execute(
        """
        INSERT INTO dq.dq_run (run_id, dataset, started_at, finished_at,
                               status, total_records)
        VALUES ('RUN-TEST', 'stg_customer', now(), now(), 'COMPLETED', 1000)
        """
    )
    conn.close()

    run_engine("golden/cross_source_consistency.py", pipeline_db)
    run_engine("review/review_queue_builder.py", pipeline_db)
    run_engine("scoring/cdp_quality_score.py", pipeline_db)

    connection = duckdb.connect(str(pipeline_db))
    yield connection, pipeline_db
    connection.close()


def test_cdp_score_is_produced_with_a_band(cdp_scored):
    conn, _ = cdp_scored
    row = conn.execute(
        """
        SELECT overall_score, overall_status, assessed_entities, total_entities
        FROM cdp.cdp_quality_score WHERE run_id = 'RUN-TEST'
        """
    ).fetchone()

    assert row is not None
    assert 0 <= row[0] <= 100
    assert row[1] in {"EXCELLENT", "GOOD", "WARNING", "POOR"}


def test_unassessed_entities_are_reported_as_coverage_not_zeros(cdp_scored):
    """The REVIEW entity must reduce coverage, not the average."""
    conn, _ = cdp_scored
    assessed, total = conn.execute(
        """
        SELECT assessed_entities, total_entities
        FROM cdp.cdp_quality_score WHERE run_id = 'RUN-TEST'
        """
    ).fetchone()

    assert assessed < total, "the REVIEW entity should be outside the assessed set"

    golden_quality = conn.execute(
        """
        SELECT score FROM cdp.cdp_quality_dimension
        WHERE run_id = 'RUN-TEST' AND dimension = 'golden_quality'
        """
    ).fetchone()[0]

    assert golden_quality == 100.0, "assessed entities all scored 100"


def test_dimensions_sum_to_the_overall_score(cdp_scored):
    conn, _ = cdp_scored
    overall = conn.execute(
        "SELECT overall_score FROM cdp.cdp_quality_score WHERE run_id = 'RUN-TEST'"
    ).fetchone()[0]

    total = conn.execute(
        """
        SELECT SUM(contribution) FROM cdp.cdp_quality_dimension
        WHERE run_id = 'RUN-TEST' AND contribution IS NOT NULL
        """
    ).fetchone()[0]

    assert abs(overall - total) < 0.01


# ------------------------------------------------------------- the gate


def test_critical_breach_blocks_regardless_of_score(cdp_scored):
    """Zero tolerance: one affected record blocks a healthy-looking CDP."""
    conn, pipeline_db = cdp_scored
    conn.execute(
        """
        INSERT INTO dq.dq_summary (
            run_id, dataset, dimension, total_records, failed_records,
            pass_rate, status, rule_id, severity, threshold, metric_type
        )
        VALUES ('RUN-TEST', 'stg_customer', 'Completeness', 1000, 1,
                99.9, 'FAIL', 'DQ-CUS-003', 'CRITICAL', 100, 'PASS_RATE')
        """
    )
    conn.close()

    module = run_engine("scoring/cdp_quality_gate.py", pipeline_db)

    connection = duckdb.connect(str(pipeline_db))
    try:
        status, blocking = connection.execute(
            """
            SELECT gate_status, blocking_reasons
            FROM cdp.cdp_quality_gate WHERE run_id = 'RUN-TEST'
            """
        ).fetchone()
        assert status == "BLOCKED"
        assert "DQ-CUS-003" in blocking
    finally:
        connection.close()


def test_gate_verdict_is_persisted_for_audit(cdp_scored):
    conn, pipeline_db = cdp_scored
    conn.close()

    run_engine("scoring/cdp_quality_gate.py", pipeline_db)

    connection = duckdb.connect(str(pipeline_db))
    try:
        row = connection.execute(
            "SELECT gate_status, evaluated_at FROM cdp.cdp_quality_gate"
        ).fetchone()
        assert row is not None
        assert row[1] is not None
    finally:
        connection.close()
