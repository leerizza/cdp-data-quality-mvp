"""Phase 5 - the review case model.

review_queue stays the record of detected issues. A case is the question
a steward actually answers, and several issues can be evidence for one
question.
"""

from __future__ import annotations

import duckdb
import pytest

from conftest import golden_of, run_engine


@pytest.fixture
def cased(pipeline_db):
    run_engine("identity/identity_candidate_generator.py", pipeline_db)
    run_engine("identity/identity_decision.py", pipeline_db)
    run_engine("identity/identity_clustering.py", pipeline_db)
    run_engine("source_dq/attribute_dq.py", pipeline_db)
    run_engine("golden/survivorship_engine.py", pipeline_db)
    run_engine("golden/cross_source_consistency.py", pipeline_db)
    run_engine("review/review_queue_builder.py", pipeline_db)
    run_engine("review/review_case_builder.py", pipeline_db)

    conn = duckdb.connect(str(pipeline_db))
    yield conn, pipeline_db
    conn.close()


def case_for(conn, subject_key):
    return conn.execute(
        """
        SELECT case_id, subject_type, severity, status, evidence_count, issue_types
        FROM cdp.review_case WHERE subject_key = ?
        """,
        [subject_key],
    ).fetchone()


# ------------------------------------------------------------- grouping


def test_cases_are_fewer_than_the_issues_they_group(cased):
    conn, _ = cased
    cases = conn.execute("SELECT COUNT(*) FROM cdp.review_case").fetchone()[0]
    issues = conn.execute("SELECT COUNT(*) FROM cdp.review_queue").fetchone()[0]

    assert cases > 0
    assert cases < issues, "grouping should collapse evidence, not mirror it"


def test_one_entity_conflict_becomes_one_case(cased):
    """G000003 raises an attribute conflict and two identity conflicts."""
    conn, _ = cased
    golden_id = golden_of(conn, "CRM003")
    row = case_for(conn, golden_id)

    assert row is not None
    assert row[1] == "GOLDEN_ENTITY"
    assert row[4] >= 3, "all three issues should be evidence on one case"
    assert "IDENTITY_CONFLICT" in row[5]
    assert "ATTRIBUTE_CONFLICT" in row[5]


def test_every_review_is_attached_to_exactly_one_case(cased):
    """No evidence may be dropped, and none double counted."""
    conn, _ = cased
    orphans = conn.execute(
        """
        SELECT COUNT(*) FROM cdp.review_queue rq
        LEFT JOIN cdp.review_case_member m ON m.review_id = rq.review_id
        WHERE m.review_id IS NULL
        """
    ).fetchone()[0]
    assert orphans == 0

    duplicated = conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT review_id FROM cdp.review_case_member
            GROUP BY review_id HAVING COUNT(*) > 1
        )
        """
    ).fetchone()[0]
    assert duplicated == 0


def test_possible_matches_group_under_the_entity_the_source_is_in(pipeline_db):
    """CRM002's possible match belongs to CRM002's entity, not its own case."""
    run_engine("identity/identity_candidate_generator.py", pipeline_db)
    run_engine("identity/identity_decision.py", pipeline_db)
    run_engine("identity/identity_clustering.py", pipeline_db)
    run_engine("review/review_queue_builder.py", pipeline_db)
    run_engine("review/review_case_builder.py", pipeline_db)

    conn = duckdb.connect(str(pipeline_db))
    try:
        target = golden_of(conn, "CRM002")
        row = conn.execute(
            """
            SELECT c.subject_type, c.subject_key
            FROM cdp.review_case_member m
            JOIN cdp.review_case c ON c.case_id = m.case_id
            JOIN cdp.review_queue rq ON rq.review_id = m.review_id
            WHERE rq.source_customer_id = 'CRM002'
              AND rq.issue_type = 'POSSIBLE_MATCH'
            """
        ).fetchone()
        assert row == ("GOLDEN_ENTITY", target)
    finally:
        conn.close()


# ------------------------------------------------------------- roll-up


def test_case_severity_is_the_worst_of_its_evidence(cased):
    conn, _ = cased
    rows = conn.execute(
        """
        SELECT c.case_id, c.severity, MAX(m.severity)
        FROM cdp.review_case c
        JOIN cdp.review_case_member m ON m.case_id = c.case_id
        WHERE m.severity IN ('HIGH', 'MEDIUM', 'LOW')
        GROUP BY c.case_id, c.severity
        """
    ).fetchall()

    assert rows
    for case_id, case_severity, _ in rows:
        members = conn.execute(
            "SELECT severity FROM cdp.review_case_member WHERE case_id = ?",
            [case_id],
        ).fetchall()
        if any(s[0] == "HIGH" for s in members):
            assert case_severity == "HIGH"


def test_case_is_open_while_any_evidence_is_open(cased):
    conn, _ = cased
    rows = conn.execute(
        """
        SELECT c.status, COUNT(*) FILTER (WHERE rq.status = 'OPEN')
        FROM cdp.review_case c
        JOIN cdp.review_case_member m ON m.case_id = c.case_id
        JOIN cdp.review_queue rq ON rq.review_id = m.review_id
        GROUP BY c.case_id, c.status
        """
    ).fetchall()

    for status, open_count in rows:
        assert status == ("OPEN" if open_count else "RESOLVED")


# --------------------------------------------------------- idempotency


def test_rebuilding_keeps_the_same_case_ids(cased):
    conn, pipeline_db = cased
    before = conn.execute(
        "SELECT case_id, subject_key FROM cdp.review_case ORDER BY case_id"
    ).fetchall()
    conn.close()

    run_engine("review/review_case_builder.py", pipeline_db)

    connection = duckdb.connect(str(pipeline_db))
    try:
        after = connection.execute(
            "SELECT case_id, subject_key FROM cdp.review_case ORDER BY case_id"
        ).fetchall()
        assert before == after
    finally:
        connection.close()


def test_review_queue_is_left_untouched(cased):
    """The case layer must sit above the queue, not rewrite it."""
    conn, pipeline_db = cased
    before = conn.execute(
        "SELECT review_id, issue_type, status FROM cdp.review_queue ORDER BY review_id"
    ).fetchall()
    conn.close()

    run_engine("review/review_case_builder.py", pipeline_db)

    connection = duckdb.connect(str(pipeline_db))
    try:
        after = connection.execute(
            "SELECT review_id, issue_type, status FROM cdp.review_queue ORDER BY review_id"
        ).fetchall()
        assert before == after
    finally:
        connection.close()
