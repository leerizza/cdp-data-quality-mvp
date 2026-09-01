"""Phase 3 - survivorship and attribute provenance.

The rule under test: DQ eligibility is applied before source priority.
A source that fails attribute DQ cannot win, however high its priority.
"""

from __future__ import annotations

import duckdb
import pytest

from conftest import approve, golden_of, run_engine


@pytest.fixture
def survived(pipeline_db):
    """Cluster, approve MOB002 into CRM002's entity, then survive."""
    run_engine("identity/identity_candidate_generator.py", pipeline_db)
    run_engine("identity/identity_decision.py", pipeline_db)
    run_engine("identity/identity_clustering.py", pipeline_db)

    conn = duckdb.connect(str(pipeline_db))
    target = golden_of(conn, "CRM002")
    approve(conn, "MOBILE", "MOB002", target)
    conn.close()

    run_engine("identity/identity_clustering.py", pipeline_db)
    run_engine("source_dq/attribute_dq.py", pipeline_db)
    run_engine("golden/survivorship_engine.py", pipeline_db)

    conn = duckdb.connect(str(pipeline_db))
    yield conn, target
    conn.close()


def attribute(conn, golden_id, name):
    return conn.execute(
        """
        SELECT attribute_value, source_system, source_customer_id,
               source_priority, dq_eligible
        FROM cdp.golden_customer_attribute
        WHERE golden_id = ? AND attribute_name = ?
        """,
        [golden_id, name],
    ).fetchone()


# --------------------------------------------------------- attribute DQ


def test_mob002_has_no_nik_and_fails_attribute_dq(survived):
    conn, _ = survived
    row = conn.execute(
        """
        SELECT status FROM dq.dq_attribute_result
        WHERE source_customer_id = 'MOB002' AND attribute_name = 'nik'
        """
    ).fetchone()
    assert row is not None
    assert row[0] == "FAIL"


def test_mob002_other_attributes_still_pass(survived):
    """One bad attribute must not condemn the whole record."""
    conn, _ = survived
    rows = conn.execute(
        """
        SELECT attribute_name, status FROM dq.dq_attribute_result
        WHERE source_customer_id = 'MOB002' AND attribute_name <> 'nik'
        """
    ).fetchall()
    assert rows
    assert all(status == "PASS" for _, status in rows)


# --------------------------------------------------------- survivorship


def test_only_dq_eligible_values_survive(survived):
    conn, _ = survived
    ineligible = conn.execute(
        "SELECT COUNT(*) FROM cdp.golden_customer_attribute WHERE dq_eligible = FALSE"
    ).fetchone()[0]
    assert ineligible == 0


def test_failing_source_loses_even_though_it_is_a_member(survived):
    """MOB002 is in the entity but its NIK failed, so CRM002's NIK wins."""
    conn, target = survived
    row = attribute(conn, target, "nik")

    assert row is not None
    assert row[1] == "CRM"
    assert row[2] == "CRM002"
    assert row[4] is True


def test_priority_still_decides_among_eligible_sources(survived):
    """MOB002's phone passed DQ, and MOBILE is priority 1 for phone."""
    conn, target = survived
    row = attribute(conn, target, "phone")

    assert row is not None
    assert (row[1], row[2]) == ("MOBILE", "MOB002")
    assert row[3] == 1


def test_birth_date_follows_the_configured_priority(survived):
    """LOS is priority 1 for birth_date."""
    conn, target = survived
    row = attribute(conn, target, "birth_date")

    assert row is not None
    assert row[1] == "LOS"


def test_provenance_records_why_each_value_won(survived):
    conn, target = survived
    rows = conn.execute(
        """
        SELECT attribute_name, selection_reason
        FROM cdp.golden_customer_attribute WHERE golden_id = ?
        """,
        [target],
    ).fetchall()

    assert rows
    for name, reason in rows:
        assert reason, f"{name} has no selection reason"
        assert "DQ=PASS" in reason


def test_survivorship_is_idempotent(pipeline_db, survived):
    conn, target = survived
    before = conn.execute(
        """
        SELECT attribute_name, attribute_value, source_customer_id
        FROM cdp.golden_customer_attribute ORDER BY golden_id, attribute_name
        """
    ).fetchall()

    run_engine("golden/survivorship_engine.py", pipeline_db)

    after = conn.execute(
        """
        SELECT attribute_name, attribute_value, source_customer_id
        FROM cdp.golden_customer_attribute ORDER BY golden_id, attribute_name
        """
    ).fetchall()

    assert before == after
