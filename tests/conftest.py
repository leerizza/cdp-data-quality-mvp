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
