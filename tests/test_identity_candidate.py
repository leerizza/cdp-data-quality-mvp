"""Phase 3 - candidate generation and pair decisions.

Runs the real scripts against a temp database seeded with the 15 source
records, so these assert on the pipeline's behaviour rather than on a
reimplementation of it.
"""

from __future__ import annotations

import duckdb
import pytest

from conftest import run_engine


@pytest.fixture
def candidates_built(pipeline_db):
    run_engine("identity/identity_candidate_generator.py", pipeline_db)
    conn = duckdb.connect(str(pipeline_db))
    yield conn
    conn.close()


@pytest.fixture
def decided(pipeline_db):
    run_engine("identity/identity_candidate_generator.py", pipeline_db)
    run_engine("identity/identity_decision.py", pipeline_db)
    conn = duckdb.connect(str(pipeline_db))
    yield conn
    conn.close()


def score_for(conn, left: str, right: str):
    row = conn.execute(
        """
        SELECT score FROM cdp.identity_match_candidate
        WHERE source_customer_id = ? AND candidate_source_customer_id = ?
        """,
        [left, right],
    ).fetchone()
    return row[0] if row else None


def status_for(conn, left: str, right: str):
    row = conn.execute(
        """
        SELECT match_status FROM cdp.identity_match
        WHERE source_customer_id = ? AND candidate_source_customer_id = ?
        """,
        [left, right],
    ).fetchone()
    return row[0] if row else None


# ------------------------------------------------------------- generation


def test_candidates_are_generated(candidates_built):
    count = candidates_built.execute(
        "SELECT COUNT(*) FROM cdp.identity_match_candidate"
    ).fetchone()[0]
    assert count > 0


def test_full_agreement_scores_highest(candidates_built):
    """CRM001/LOS001 agree on NIK, DOB, phone and email."""
    assert score_for(candidates_built, "CRM001", "LOS001") == 190


def test_email_conflict_costs_thirty_points(candidates_built):
    """CRM003/LOS003 match on NIK, DOB and phone but disagree on email.

    100 + 30 + 30 - 30 = 130.
    """
    assert score_for(candidates_built, "CRM003", "LOS003") == 130


def test_candidate_generation_is_idempotent(pipeline_db):
    run_engine("identity/identity_candidate_generator.py", pipeline_db)
    conn = duckdb.connect(str(pipeline_db))
    first = conn.execute("SELECT COUNT(*) FROM cdp.identity_match_candidate").fetchone()[0]
    conn.close()

    run_engine("identity/identity_candidate_generator.py", pipeline_db)
    conn = duckdb.connect(str(pipeline_db))
    second = conn.execute("SELECT COUNT(*) FROM cdp.identity_match_candidate").fetchone()[0]
    conn.close()

    assert first == second


# --------------------------------------------------------------- decision


def test_strong_agreement_is_a_match(decided):
    assert status_for(decided, "CRM001", "LOS001") == "MATCH"


def test_email_conflict_is_a_conflict_even_with_a_high_score(decided):
    """Conflict must win over score: 130 would otherwise be a MATCH."""
    assert status_for(decided, "CRM003", "LOS003") == "CONFLICT"


def test_supporting_signals_without_nik_are_a_possible_match(decided):
    """MOB002 has no NIK, but DOB, phone and email agree with CRM002."""
    assert status_for(decided, "CRM002", "MOB002") == "POSSIBLE_MATCH"


def test_unrelated_records_produce_no_trusted_match(decided):
    """CRM005 shares nothing with the other sources."""
    row = decided.execute(
        """
        SELECT COUNT(*) FROM cdp.identity_match
        WHERE source_customer_id = 'CRM005' AND match_status = 'MATCH'
        """
    ).fetchone()[0]
    assert row == 0
