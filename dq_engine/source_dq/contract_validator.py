"""Data contract validation - the shape check that runs before anything loads.

Every rule in the registry asks a question about the contents of a row.
None of them survives the column it names disappearing: if CRM stops
sending `email`, dbt seed still loads the file happily and
stg_crm_customer.sql dies on a binder error. The pipeline is then over
before the DQ engine starts - no verdict, no incident, nothing for the
source gate to block on. A stack trace is not a quality signal.

This runs first, reads the files as raw text, and compares them against
metadata/data_contract.csv:

    FILE_MISSING     a contracted file is not there at all
    MISSING_COLUMN   a required column is gone (or was renamed)
    NEW_COLUMN       an undeclared column appeared
    TYPE_MISMATCH    values no longer cast to the declared type
    NULL_VIOLATION   a key column declared NOT NULL has empty values

The first two are structural and always CRITICAL: nothing downstream can
run without the column, so the contract's own severity - which grades how
much the attribute's *content* matters - has no say. The last two are
graded by the contract, because the file still loads.

The seed directory is what gets validated, not data/, because the seeds
are what dbt actually loads into the warehouse. Validating the landing
copy would leave the gap open whenever the two drift apart.

Types are checked with TRY_CAST against the declared type rather than by
letting DuckDB infer one. Inference is content-dependent - an all-digit
`nik` column infers as BIGINT even though the contract says VARCHAR - so
inference would report drift that is not there and miss drift that is.
A declared VARCHAR accepts anything by definition, so only the narrower
declarations (DATE here) can actually fail.

Like both other gates, this reports by default; main.py --strict is what
makes BLOCKED stop the run.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[2]

DB_PATH = PROJECT_ROOT / "database" / "cdp.duckdb"
CONTRACT_FILE = PROJECT_ROOT / "metadata" / "data_contract.csv"
SEED_DIR = PROJECT_ROOT / "dbt" / "cdp_dq" / "seeds"

# An undeclared column breaks nothing downstream - the staging models
# select named columns - but it is still drift a steward should see.
NEW_COLUMN_SEVERITY = "LOW"

# A required column that is absent is always CRITICAL, whatever severity
# the contract gives it. The severity column grades how much the *content*
# of an attribute matters (a bad email is milder than a bad NIK); a column
# that is not there is a different question entirely - every model that
# names it fails to compile, so the load cannot proceed at any severity.
# That is the same zero-tolerance stance the rule registry takes.
STRUCTURAL_SEVERITY = "CRITICAL"


# ------------------------------------------------------------- contracts


def load_contracts(conn) -> list[dict]:
    """Refresh dq.data_contract from metadata and return the enforced rows."""
    if not CONTRACT_FILE.exists():
        raise FileNotFoundError(f"Contract file not found: {CONTRACT_FILE}")

    conn.execute("DELETE FROM dq.data_contract")
    conn.execute(
        """
        INSERT INTO dq.data_contract (
            contract_id, source_system, file_name, column_name, data_type,
            is_nullable, is_required, severity, status, owner, loaded_at
        )
        SELECT
            contract_id,
            source_system,
            file_name,
            column_name,
            upper(data_type),
            CAST(is_nullable AS BOOLEAN),
            CAST(is_required AS BOOLEAN),
            severity,
            status,
            owner,
            CURRENT_TIMESTAMP
        FROM read_csv_auto(?)
        """,
        [str(CONTRACT_FILE)],
    )

    # DRAFT contracts are registered but not enforced, mirroring how the
    # rule registry carries rules that are not APPROVED yet.
    rows = conn.execute(
        """
        SELECT source_system, file_name, column_name, data_type,
               is_nullable, is_required, severity
        FROM dq.data_contract
        WHERE status = 'APPROVED'
        ORDER BY file_name, contract_id
        """
    ).fetchall()

    return [
        {
            "source_system": r[0],
            "file_name": r[1],
            "column_name": r[2],
            "data_type": r[3],
            "is_nullable": r[4],
            "is_required": r[5],
            "severity": r[6],
        }
        for r in rows
    ]


def group_by_file(contracts: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for contract in contracts:
        grouped.setdefault(contract["file_name"], []).append(contract)
    return grouped


# -------------------------------------------------------------- checking


def read_header(conn, path: Path) -> list[str]:
    """Column names as they appear in the file.

    all_varchar keeps DuckDB from typing anything here; typing is the
    contract's job, not the reader's.
    """
    rows = conn.execute(
        "DESCRIBE SELECT * FROM read_csv_auto(?, all_varchar = true)",
        [str(path)],
    ).fetchall()
    return [row[0] for row in rows]


def count_cast_failures(conn, path: Path, column: str, data_type: str) -> int:
    """Non-empty values that do not survive a cast to the declared type."""
    return conn.execute(
        f"""
        SELECT COUNT(*)
        FROM read_csv_auto(?, all_varchar = true)
        WHERE "{column}" IS NOT NULL
          AND trim("{column}") <> ''
          AND TRY_CAST(trim("{column}") AS {data_type}) IS NULL
        """,
        [str(path)],
    ).fetchone()[0]


def count_nulls(conn, path: Path, column: str) -> int:
    """Empty string counts as null - a CSV has no way to say otherwise."""
    return conn.execute(
        f"""
        SELECT COUNT(*)
        FROM read_csv_auto(?, all_varchar = true)
        WHERE "{column}" IS NULL OR trim("{column}") = ''
        """,
        [str(path)],
    ).fetchone()[0]


def check_file(conn, file_name: str, contracts: list[dict]) -> list[dict]:
    """Every violation for one contracted file."""
    source_system = contracts[0]["source_system"]
    path = SEED_DIR / file_name

    def violation(**kwargs) -> dict:
        return {
            "source_system": source_system,
            "file_name": file_name,
            **kwargs,
        }

    if not path.exists():
        return [
            violation(
                column_name=None,
                violation_type="FILE_MISSING",
                severity="CRITICAL",
                expected=file_name,
                observed=None,
                affected_records=None,
                message=f"contracted file {file_name} is not in {SEED_DIR.name}/",
            )
        ]

    found = []
    header = read_header(conn, path)
    declared = {c["column_name"] for c in contracts}

    for contract in contracts:
        column = contract["column_name"]

        if column not in header:
            # Only required columns are a violation; an optional column
            # that never arrived is the contract working as written.
            if contract["is_required"]:
                found.append(
                    violation(
                        column_name=column,
                        violation_type="MISSING_COLUMN",
                        severity=STRUCTURAL_SEVERITY,
                        expected=column,
                        observed=", ".join(header),
                        affected_records=None,
                        message=(
                            f"required column '{column}' is absent - "
                            f"dropped or renamed upstream"
                        ),
                    )
                )
            # Nothing else can be checked about a column that is not there.
            continue

        # A declared VARCHAR cannot be violated: every CSV value is text.
        if contract["data_type"] != "VARCHAR":
            failures = count_cast_failures(conn, path, column, contract["data_type"])
            if failures:
                found.append(
                    violation(
                        column_name=column,
                        violation_type="TYPE_MISMATCH",
                        severity=contract["severity"],
                        expected=contract["data_type"],
                        observed=sample_value(conn, path, column, contract["data_type"]),
                        affected_records=failures,
                        message=(
                            f"{failures} value(s) in '{column}' do not cast to "
                            f"{contract['data_type']}"
                        ),
                    )
                )

        if not contract["is_nullable"]:
            nulls = count_nulls(conn, path, column)
            if nulls:
                found.append(
                    violation(
                        column_name=column,
                        violation_type="NULL_VIOLATION",
                        severity=contract["severity"],
                        expected="NOT NULL",
                        observed="NULL",
                        affected_records=nulls,
                        message=(
                            f"{nulls} row(s) have no '{column}', which the "
                            f"contract declares NOT NULL"
                        ),
                    )
                )

    for column in header:
        if column not in declared:
            found.append(
                violation(
                    column_name=column,
                    violation_type="NEW_COLUMN",
                    severity=NEW_COLUMN_SEVERITY,
                    expected=None,
                    observed=column,
                    affected_records=None,
                    message=f"undeclared column '{column}' appeared in {file_name}",
                )
            )

    return found


def sample_value(conn, path: Path, column: str, data_type: str) -> str | None:
    """One offending value, so the violation can be read without the file."""
    row = conn.execute(
        f"""
        SELECT trim("{column}")
        FROM read_csv_auto(?, all_varchar = true)
        WHERE "{column}" IS NOT NULL
          AND trim("{column}") <> ''
          AND TRY_CAST(trim("{column}") AS {data_type}) IS NULL
        LIMIT 1
        """,
        [str(path)],
    ).fetchone()
    return row[0] if row else None


# --------------------------------------------------------------- persist


def record(conn, contract_run_id: str, violations: list[dict]) -> None:
    for item in violations:
        conn.execute(
            """
            INSERT INTO dq.contract_violation (
                violation_id, contract_run_id, source_system, file_name,
                column_name, violation_type, severity, expected, observed,
                affected_records, message
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                str(uuid4()),
                contract_run_id,
                item["source_system"],
                item["file_name"],
                item["column_name"],
                item["violation_type"],
                item["severity"],
                item["expected"],
                item["observed"],
                item["affected_records"],
                item["message"],
            ],
        )


# ------------------------------------------------------------------ main


def main() -> int:
    conn = duckdb.connect(str(DB_PATH))

    contracts = load_contracts(conn)
    by_file = group_by_file(contracts)

    contract_run_id = str(uuid4())
    violations: list[dict] = []

    for file_name in sorted(by_file):
        violations.extend(check_file(conn, file_name, by_file[file_name]))

    critical = sum(1 for v in violations if v["severity"] == "CRITICAL")
    gate_status = "BLOCKED" if critical else ("WARNING" if violations else "PASSED")

    record(conn, contract_run_id, violations)
    conn.execute(
        """
        INSERT INTO dq.contract_run (
            contract_run_id, started_at, files_checked,
            violations, critical_violations, gate_status
        )
        VALUES (?, now(), ?, ?, ?, ?)
        """,
        [contract_run_id, len(by_file), len(violations), critical, gate_status],
    )

    print("\n=== DATA CONTRACT ===")
    print(f"Contract run   : {contract_run_id}")
    print(f"Files checked  : {len(by_file)}")
    print(f"Columns under contract: {len(contracts)}")
    print(f"Violations     : {len(violations)} ({critical} CRITICAL)")
    print(f"Gate status    : {gate_status}")

    if violations:
        print("\n=== VIOLATIONS ===")
        print(
            conn.sql(
                f"""
                SELECT
                    source_system,
                    file_name,
                    column_name,
                    violation_type,
                    severity,
                    affected_records,
                    message
                FROM dq.contract_violation
                WHERE contract_run_id = '{contract_run_id}'
                ORDER BY
                    CASE severity
                        WHEN 'CRITICAL' THEN 1
                        WHEN 'HIGH' THEN 2
                        WHEN 'MEDIUM' THEN 3
                        ELSE 4
                    END,
                    file_name,
                    column_name
                """
            )
        )
    else:
        print("\nEvery contracted file matches its declared shape.")

    conn.close()
    return 1 if gate_status == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
