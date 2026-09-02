"""Phase 7 - data contracts.

The point of the contract layer is the failure the rule engine cannot
see: a column that is gone. These tests build deliberately broken seed
files and pin that the drift is reported as a violation with a verdict,
rather than reaching dbt and dying there.

Each test writes its own seed directory and repoints the validator at
it, so nothing here depends on the project's real files.
"""

from __future__ import annotations

import duckdb
import pytest

from conftest import load_module


CONTRACT = """\
contract_id,source_system,file_name,column_name,data_type,is_nullable,is_required,severity,status,owner
DC-T-001,CRM,crm_customer.csv,source_customer_id,VARCHAR,FALSE,TRUE,CRITICAL,APPROVED,CRM Team
DC-T-002,CRM,crm_customer.csv,nik,VARCHAR,TRUE,TRUE,CRITICAL,APPROVED,CRM Team
DC-T-003,CRM,crm_customer.csv,full_name,VARCHAR,TRUE,TRUE,HIGH,APPROVED,CRM Team
DC-T-004,CRM,crm_customer.csv,phone,VARCHAR,TRUE,TRUE,HIGH,APPROVED,CRM Team
DC-T-005,CRM,crm_customer.csv,email,VARCHAR,TRUE,TRUE,MEDIUM,APPROVED,CRM Team
DC-T-006,CRM,crm_customer.csv,birth_date,DATE,TRUE,TRUE,HIGH,APPROVED,CRM Team
"""

CLEAN_CSV = """\
source_customer_id,nik,full_name,phone,email,birth_date
CRM001,3217046418903111,H. Rafid Kuswoyo,081703214070,dlestari@example.net,1977-08-19
CRM002,3257040351894191,Dr. Dewi Pratiwi,085273063633,phalimah@example.org,1993-02-22
"""


@pytest.fixture
def validator(tmp_path, db_path):
    """The validator, pointed at a throwaway seed dir and contract file.

    Returns (module, write_seed) - call write_seed(text) to lay down the
    CRM file the test wants to validate.
    """
    seeds = tmp_path / "seeds"
    seeds.mkdir()

    contract_file = tmp_path / "data_contract.csv"
    contract_file.write_text(CONTRACT, encoding="utf-8")

    module = load_module("source_dq/contract_validator.py")
    module.DB_PATH = db_path
    module.SEED_DIR = seeds
    module.CONTRACT_FILE = contract_file

    def write_seed(text: str) -> None:
        (seeds / "crm_customer.csv").write_text(text, encoding="utf-8")

    return module, write_seed


def violations(db_path):
    conn = duckdb.connect(str(db_path))
    try:
        return conn.execute(
            """
            SELECT violation_type, column_name, severity, affected_records
            FROM dq.contract_violation
            ORDER BY violation_type, column_name
            """
        ).fetchall()
    finally:
        conn.close()


def gate(db_path):
    conn = duckdb.connect(str(db_path))
    try:
        return conn.execute(
            "SELECT gate_status, violations, critical_violations FROM dq.contract_run"
        ).fetchone()
    finally:
        conn.close()


# ------------------------------------------------------------- happy path


def test_matching_file_passes(validator, db_path):
    module, write_seed = validator
    write_seed(CLEAN_CSV)

    assert module.main() == 0
    assert violations(db_path) == []
    assert gate(db_path) == ("PASSED", 0, 0)


def test_new_column_is_reported_without_blocking(validator, db_path):
    """An undeclared column is drift worth seeing, but it breaks nothing.

    The staging models select named columns, so an extra one is inert -
    hence LOW, and a verdict that still lets the run continue.
    """
    module, write_seed = validator
    write_seed(
        CLEAN_CSV.replace("birth_date", "birth_date,loyalty_tier")
        .replace("1977-08-19", "1977-08-19,GOLD")
        .replace("1993-02-22", "1993-02-22,SILVER")
    )

    assert module.main() == 0
    assert violations(db_path) == [("NEW_COLUMN", "loyalty_tier", "LOW", None)]
    assert gate(db_path)[0] == "WARNING"


# ----------------------------------------------------------------- drift


def test_dropped_column_blocks(validator, db_path):
    """The failure that motivated the phase: CRM stops sending nik."""
    module, write_seed = validator
    write_seed(
        "source_customer_id,full_name,phone,email,birth_date\n"
        "CRM001,H. Rafid Kuswoyo,081703214070,dlestari@example.net,1977-08-19\n"
    )

    assert module.main() == 1

    missing = [v for v in violations(db_path) if v[0] == "MISSING_COLUMN"]
    assert missing == [("MISSING_COLUMN", "nik", "CRITICAL", None)]
    assert gate(db_path)[0] == "BLOCKED"


def test_a_missing_column_blocks_whatever_its_contract_severity(validator, db_path):
    """email is MEDIUM as an attribute, but its absence still stops the load.

    Severity grades content. A column that is not there is structural:
    stg_crm_customer.sql names it, so the model fails to compile and the
    run is over regardless of how tolerable a bad email would have been.
    """
    module, write_seed = validator
    write_seed(
        "source_customer_id,nik,full_name,phone,birth_date\n"
        "CRM001,3217046418903111,H. Rafid Kuswoyo,081703214070,1977-08-19\n"
    )

    assert module.main() == 1

    missing = [v for v in violations(db_path) if v[0] == "MISSING_COLUMN"]
    assert missing == [("MISSING_COLUMN", "email", "CRITICAL", None)]
    assert gate(db_path)[0] == "BLOCKED"


def test_renamed_column_is_reported_as_both_sides(validator, db_path):
    """A rename is a drop plus an addition; the pair is what identifies it."""
    module, write_seed = validator
    write_seed(CLEAN_CSV.replace("email", "email_address"))

    module.main()
    types = {(v[0], v[1]) for v in violations(db_path)}

    assert ("MISSING_COLUMN", "email") in types
    assert ("NEW_COLUMN", "email_address") in types


def test_retyped_column_is_caught_with_a_sample(validator, db_path):
    """birth_date arriving as dd/mm/yyyy still parses as text, not as a date."""
    module, write_seed = validator
    write_seed(
        CLEAN_CSV.replace("1977-08-19", "19/08/1977").replace("1993-02-22", "22/02/1993")
    )

    module.main()

    conn = duckdb.connect(str(db_path))
    try:
        row = conn.execute(
            """
            SELECT severity, affected_records, expected, observed
            FROM dq.contract_violation
            WHERE violation_type = 'TYPE_MISMATCH' AND column_name = 'birth_date'
            """
        ).fetchone()
    finally:
        conn.close()

    assert row == ("HIGH", 2, "DATE", "19/08/1977")


def test_null_key_violates_the_contract(validator, db_path):
    module, write_seed = validator
    write_seed(
        CLEAN_CSV.replace(
            "CRM002,3257040351894191", ",3257040351894191"
        )
    )

    assert module.main() == 1

    nulls = [v for v in violations(db_path) if v[0] == "NULL_VIOLATION"]
    assert nulls == [("NULL_VIOLATION", "source_customer_id", "CRITICAL", 1)]


def test_missing_file_blocks(validator, db_path):
    module, _ = validator  # nothing written

    assert module.main() == 1
    assert violations(db_path) == [("FILE_MISSING", None, "CRITICAL", None)]


# --------------------------------------------------------------- registry


def test_nullable_content_is_left_to_the_rule_engine(validator, db_path):
    """An empty nik is a rule's business, not the contract's.

    The contract declares nik nullable precisely so the same issue is not
    raised twice under two names; DQ-CUS-003 is what fails it.
    """
    module, write_seed = validator
    write_seed(CLEAN_CSV.replace(",3257040351894191,", ",,"))

    assert module.main() == 0
    assert [v for v in violations(db_path) if v[1] == "nik"] == []


def test_draft_contracts_are_registered_but_not_enforced(validator, db_path, tmp_path):
    module, write_seed = validator
    module.CONTRACT_FILE.write_text(
        CONTRACT.replace("DC-T-002,CRM,crm_customer.csv,nik,VARCHAR,TRUE,TRUE,CRITICAL,APPROVED",
                         "DC-T-002,CRM,crm_customer.csv,nik,VARCHAR,TRUE,TRUE,CRITICAL,DRAFT"),
        encoding="utf-8",
    )
    write_seed(CLEAN_CSV.replace(",3217046418903111,", ",").replace(",3257040351894191,", ","))

    module.main()

    conn = duckdb.connect(str(db_path))
    try:
        registered = conn.execute(
            "SELECT COUNT(*) FROM dq.data_contract"
        ).fetchone()[0]
    finally:
        conn.close()

    assert registered == 6
    assert [v for v in violations(db_path) if v[1] == "nik"] == []
