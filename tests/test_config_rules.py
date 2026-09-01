"""Phase 2 - rules come from metadata, not from the code.

The point of these tests is that editing a CSV changes behaviour. Before
this phase the CSVs existed but were decorative: the real priority table
was a dict in survivorship_engine.py, so editing metadata did nothing.
"""

from __future__ import annotations

import pytest

from conftest import load_module


@pytest.fixture
def survivorship():
    return load_module("golden/survivorship_engine.py")


@pytest.fixture
def candidates():
    return load_module("identity/identity_candidate_generator.py")


@pytest.fixture
def decision():
    return load_module("identity/identity_decision.py")


# ------------------------------------------------------- survivorship rules


def test_source_priority_loads_every_attribute(survivorship, conn):
    priority = survivorship.load_source_priority(conn)

    assert set(priority) == {"nik", "full_name", "phone", "email", "birth_date"}


def test_shipped_priorities_match_the_documented_policy(survivorship, conn):
    priority = survivorship.load_source_priority(conn)

    assert priority["phone"] == {"MOBILE": 1, "CRM": 2, "LOS": 3}
    assert priority["nik"] == {"CRM": 1, "LOS": 2, "MOBILE": 3}
    assert priority["birth_date"] == {"LOS": 1, "CRM": 2, "MOBILE": 3}


def test_editing_the_csv_changes_the_priority(survivorship, conn, tmp_path):
    """The behaviour this phase exists to provide."""
    csv = tmp_path / "survivorship_rule.csv"
    csv.write_text(
        "attribute_name,source_system,priority,status,description\n"
        "phone,CRM,1,ACTIVE,\n"
        "phone,MOBILE,2,ACTIVE,\n",
        encoding="utf-8",
    )
    survivorship.SURVIVORSHIP_RULE_CSV = csv

    priority = survivorship.load_source_priority(conn)

    assert priority["phone"]["CRM"] == 1
    assert priority["phone"]["MOBILE"] == 2


def test_non_active_rules_are_ignored(survivorship, conn, tmp_path):
    csv = tmp_path / "survivorship_rule.csv"
    csv.write_text(
        "attribute_name,source_system,priority,status,description\n"
        "phone,CRM,1,ACTIVE,\n"
        "phone,MOBILE,2,DRAFT,not yet approved\n",
        encoding="utf-8",
    )
    survivorship.SURVIVORSHIP_RULE_CSV = csv

    priority = survivorship.load_source_priority(conn)

    assert priority["phone"] == {"CRM": 1}


def test_empty_rule_file_is_an_error(survivorship, conn, tmp_path):
    """Silently scoring nothing would quietly empty the golden layer."""
    csv = tmp_path / "survivorship_rule.csv"
    csv.write_text(
        "attribute_name,source_system,priority,status,description\n"
        "phone,CRM,1,DRAFT,\n",
        encoding="utf-8",
    )
    survivorship.SURVIVORSHIP_RULE_CSV = csv

    with pytest.raises(RuntimeError, match="No ACTIVE survivorship rules"):
        survivorship.load_source_priority(conn)


# ---------------------------------------------------------- identity scores


def test_signal_scores_match_the_previous_hardcoded_values(candidates, conn):
    """Behaviour must not change: these are the values the code used."""
    scores = candidates.load_signal_scores(conn)

    assert scores["NIK_MATCH"] == 100
    assert scores["DOB_MATCH"] == 30
    assert scores["PHONE_MATCH"] == 30
    assert scores["EMAIL_MATCH"] == 30
    assert scores["DOB_CONFLICT"] == -50
    assert scores["PHONE_CONFLICT"] == -30
    assert scores["EMAIL_CONFLICT"] == -30


def test_conflict_scores_are_negative_so_they_can_be_added(candidates, conn):
    scores = candidates.load_signal_scores(conn)

    assert all(scores[s] < 0 for s in ("DOB_CONFLICT", "PHONE_CONFLICT", "EMAIL_CONFLICT"))


def test_missing_signal_is_an_error_not_a_zero(candidates, conn, tmp_path):
    csv = tmp_path / "identity_score_rule.csv"
    csv.write_text(
        "signal,score,status,description\nNIK_MATCH,100,ACTIVE,\n",
        encoding="utf-8",
    )
    candidates.IDENTITY_SCORE_CSV = csv

    with pytest.raises(RuntimeError, match="missing ACTIVE signal"):
        candidates.load_signal_scores(conn)


# -------------------------------------------------------------- thresholds


def test_decision_thresholds_match_previous_behaviour(decision, conn):
    thresholds = decision.load_thresholds(conn, "identity_decision")

    assert thresholds["match_min_score"] == 100
    assert thresholds["possible_match_min_score"] == 60
    assert thresholds["possible_match_min_signals"] == 2


def test_unknown_scope_is_an_error(decision, conn):
    with pytest.raises(RuntimeError, match="No ACTIVE thresholds"):
        decision.load_thresholds(conn, "no_such_scope")


def test_decision_still_classifies_the_known_pairs(decision, conn):
    """CRM001/LOS001 style MATCH and CRM002/MOB002 style POSSIBLE_MATCH."""
    thresholds = decision.load_thresholds(conn, "identity_decision")

    # NIK + DOB + PHONE + EMAIL, no conflict -> 190
    strong = [True, True, True, True, False, False, False, 190]
    assert decision.determine_decision(strong, thresholds) == "MATCH"

    # No NIK, but DOB + PHONE + EMAIL agree -> 90
    supporting = [False, True, True, True, False, False, False, 90]
    assert decision.determine_decision(supporting, thresholds) == "POSSIBLE_MATCH"

    # NIK matches but email conflicts -> conflict wins over score
    conflicted = [True, True, True, False, False, False, True, 130]
    assert decision.determine_decision(conflicted, thresholds) == "CONFLICT"

    weak = [False, True, False, False, False, False, False, 30]
    assert decision.determine_decision(weak, thresholds) == "NO_MATCH"
