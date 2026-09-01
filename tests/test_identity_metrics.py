"""Phase 4 - identity resolution metrics.

The metric being replaced counted a record in a REVIEW entity as fully
resolved. These tests pin the distinction: confirmed, in review and
unresolved are three different things.
"""

from __future__ import annotations

import duckdb
import pytest

from conftest import load_module, run_engine


@pytest.fixture
def metrics_module():
    return load_module("identity/identity_metrics.py")


@pytest.fixture
def measured(pipeline_db):
    run_engine("identity/identity_candidate_generator.py", pipeline_db)
    run_engine("identity/identity_decision.py", pipeline_db)
    run_engine("identity/identity_clustering.py", pipeline_db)

    conn = duckdb.connect(str(pipeline_db))
    yield conn, pipeline_db
    conn.close()


def computed(metrics_module, conn):
    thresholds = metrics_module.load_thresholds(conn, "identity_metrics")
    return metrics_module.compute(conn, thresholds)


# ------------------------------------------------------------ pair rates


def test_pair_rates_share_one_denominator(metrics_module, measured):
    conn, _ = measured
    m = computed(metrics_module, conn)

    total = m["match_rate"] + m["possible_match_rate"] + m["conflict_rate"]
    assert m["total_pairs"] == 12
    assert total <= 100.0 + 1e-9


def test_conflict_pairs_are_counted(metrics_module, measured):
    """CRM003/LOS003 and LOS003/MOB003 conflict on email."""
    conn, _ = measured
    m = computed(metrics_module, conn)

    assert m["conflict_rate"] > 0


# ---------------------------------------------------------- record split


def test_records_split_into_confirmed_review_and_unresolved(metrics_module, measured):
    conn, _ = measured
    m = computed(metrics_module, conn)

    assert m["total_source_records"] == 15
    assert (
        m["confirmed_records"] + m["review_records"] + m["unresolved_records"]
        == m["total_source_records"]
    )


def test_review_records_are_not_counted_as_confirmed(metrics_module, measured):
    """The whole point: the 003 group is in an entity but not settled."""
    conn, _ = measured
    m = computed(metrics_module, conn)

    assert m["review_records"] == 3
    assert m["unresolved_rate"] > 0


def test_health_is_below_the_naive_resolved_ratio(metrics_module, measured):
    """The metric this replaces would report (confirmed+review)/total."""
    conn, _ = measured
    m = computed(metrics_module, conn)

    naive = (
        (m["confirmed_records"] + m["review_records"])
        / m["total_source_records"]
        * 100.0
    )
    assert m["resolution_health"] < naive


def test_health_stays_within_bounds(metrics_module, measured):
    conn, _ = measured
    m = computed(metrics_module, conn)

    assert 0.0 <= m["resolution_health"] <= 100.0


# --------------------------------------------------------- cluster shape


def test_cluster_shape_is_reported(metrics_module, measured):
    conn, _ = measured
    m = computed(metrics_module, conn)

    assert m["golden_entity_count"] > 0
    assert m["avg_cluster_size"] >= 2.0, "an entity needs at least two members"


# -------------------------------------------------------------- persistence


def test_metrics_are_written_and_idempotent(measured):
    conn, pipeline_db = measured
    conn.execute(
        """
        INSERT INTO dq.dq_run (run_id, dataset, started_at, finished_at,
                               status, total_records)
        VALUES ('RUN-TEST', 'stg_customer', now(), now(), 'COMPLETED', 1000)
        """
    )
    conn.close()

    run_engine("identity/identity_metrics.py", pipeline_db)
    run_engine("identity/identity_metrics.py", pipeline_db)

    connection = duckdb.connect(str(pipeline_db))
    try:
        rows = connection.execute(
            "SELECT COUNT(*) FROM cdp.identity_metrics WHERE run_id = 'RUN-TEST'"
        ).fetchone()[0]
        assert rows == 1, "re-running must replace the row, not add one"
    finally:
        connection.close()


def test_cdp_score_consumes_the_metrics(measured):
    """The CDP identity dimension now reads resolution_health."""
    conn, pipeline_db = measured
    conn.execute(
        """
        INSERT INTO dq.dq_run (run_id, dataset, started_at, finished_at,
                               status, total_records)
        VALUES ('RUN-TEST', 'stg_customer', now(), now(), 'COMPLETED', 1000)
        """
    )
    conn.close()

    run_engine("identity/identity_metrics.py", pipeline_db)
    run_engine("source_dq/attribute_dq.py", pipeline_db)
    run_engine("golden/survivorship_engine.py", pipeline_db)
    run_engine("golden/golden_quality_score.py", pipeline_db)
    run_engine("golden/cross_source_consistency.py", pipeline_db)
    run_engine("review/review_queue_builder.py", pipeline_db)
    run_engine("scoring/cdp_quality_score.py", pipeline_db)

    connection = duckdb.connect(str(pipeline_db))
    try:
        dimension, health = connection.execute(
            """
            SELECT d.score, m.resolution_health
            FROM cdp.cdp_quality_dimension d
            JOIN cdp.identity_metrics m ON m.run_id = d.run_id
            WHERE d.run_id = 'RUN-TEST' AND d.dimension = 'identity'
            """
        ).fetchone()
        assert dimension == health
    finally:
        connection.close()
