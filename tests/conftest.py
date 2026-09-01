"""Shared pytest fixtures.

The dq_engine scripts are standalone entry points, not an importable
package - nothing imports anything else, and main.py runs each one as its
own process. Tests therefore load them by file path.

Every fixture database is created as a file literally named
``cdp.duckdb``. DuckDB names the attached database after the file, so
each ``cdp.<table>`` reference in the engine resolves through that name.
A temp file called anything else makes every one of those references fail.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path

import duckdb
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DQ_ENGINE = PROJECT_ROOT / "dq_engine"
INIT_SQL = DQ_ENGINE / "sql" / "init_dq.sql"


def load_module(relative_path: str):
    """Import a dq_engine script by path, e.g. 'identity/identity_clustering.py'."""
    script = DQ_ENGINE / relative_path
    name = f"dqe_{script.stem}_{uuid.uuid4().hex[:8]}"

    spec = importlib.util.spec_from_file_location(name, script)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def db_path(tmp_path) -> Path:
    """An empty database with the full schema applied."""
    path = tmp_path / "cdp.duckdb"
    conn = duckdb.connect(str(path))
    try:
        conn.execute(INIT_SQL.read_text(encoding="utf-8"))
    finally:
        conn.close()
    return path


@pytest.fixture
def conn(db_path):
    connection = duckdb.connect(str(db_path))
    yield connection
    connection.close()


@pytest.fixture
def clustering():
    return load_module("identity/identity_clustering.py")


# --------------------------------------------------------------- pipeline

DATA_DIR = PROJECT_ROOT / "data"

SOURCES = [
    ("CRM", "crm_customer.csv"),
    ("LOS", "los_customer.csv"),
    ("MOBILE", "mobile_customer.csv"),
]


def build_customer_unified(conn) -> None:
    """Recreate main.customer_unified from the source CSVs.

    Mirrors the dbt models (trim everything, lowercase email) so tests
    exercise the same values the pipeline sees, without needing dbt or
    the project database to have been built first.

    Text columns are cast to VARCHAR to match the column_types the dbt
    seeds declare in properties.yaml. Left to itself read_csv_auto types
    nik as BIGINT, which both breaks trim() and drops any leading zero.
    """
    selects = []
    params: list[str] = []

    for source_system, filename in SOURCES:
        selects.append(
            f"""
            SELECT
                '{source_system}' AS source_system,
                CAST(source_customer_id AS VARCHAR) AS source_customer_id,
                trim(CAST(nik AS VARCHAR)) AS nik,
                trim(CAST(full_name AS VARCHAR)) AS full_name,
                trim(CAST(phone AS VARCHAR)) AS phone,
                lower(trim(CAST(email AS VARCHAR))) AS email,
                CAST(birth_date AS DATE) AS birth_date
            FROM read_csv_auto(?, all_varchar = true)
            """
        )
        params.append(str(DATA_DIR / filename))

    conn.execute(
        "CREATE OR REPLACE TABLE main.customer_unified AS "
        + " UNION ALL ".join(selects),
        params,
    )


@pytest.fixture
def pipeline_db(tmp_path):
    """A database with the schema and the 15 unified source records.

    Returns the path; run engine scripts against it with run_engine().
    """
    path = tmp_path / "cdp.duckdb"
    connection = duckdb.connect(str(path))
    try:
        connection.execute(INIT_SQL.read_text(encoding="utf-8"))
        build_customer_unified(connection)
    finally:
        connection.close()
    return path


def run_engine(relative_path: str, db_path: Path):
    """Run one engine script against a specific database.

    Each script resolves its own DB_PATH at import time, so the module is
    loaded fresh and repointed before main() runs.
    """
    module = load_module(relative_path)
    module.DB_PATH = db_path
    module.main()
    return module


def approve(conn, source_system: str, source_customer_id: str, golden_id: str) -> None:
    """Record a steward approval, the input clustering treats as trusted."""
    conn.execute(
        """
        INSERT INTO cdp.identity_resolution_action (
            action_id, review_id, action_type, source_system,
            source_customer_id, golden_id, performed_by, reason
        )
        VALUES (?, ?, 'APPROVED', ?, ?, ?, 'TEST_STEWARD', 'test fixture')
        """,
        [
            str(uuid.uuid4()),
            str(uuid.uuid4()),
            source_system,
            source_customer_id,
            golden_id,
        ],
    )


def golden_of(conn, source_customer_id: str):
    """The golden_id a source record ended up in, or None if unresolved."""
    row = conn.execute(
        """
        SELECT golden_id
        FROM cdp.golden_entity_member
        WHERE source_customer_id = ?
        """,
        [source_customer_id],
    ).fetchone()
    return row[0] if row else None


def add_member(conn, golden_id: str, source_system: str, source_customer_id: str,
               status: str = "CONFIRMED", confidence: str = "HIGH") -> None:
    """Insert a golden entity member, creating the entity if needed."""
    conn.execute(
        """
        INSERT INTO cdp.golden_entity (
            golden_id, entity_type, entity_status, has_conflict, confidence
        )
        VALUES (?, 'CUSTOMER', ?, FALSE, ?)
        ON CONFLICT (golden_id) DO NOTHING
        """,
        [golden_id, "ACTIVE" if status == "CONFIRMED" else "REVIEW", confidence],
    )
    conn.execute(
        """
        INSERT INTO cdp.golden_entity_member (
            golden_id, source_system, source_customer_id,
            membership_status, membership_confidence
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT DO NOTHING
        """,
        [golden_id, source_system, source_customer_id, status, confidence],
    )
