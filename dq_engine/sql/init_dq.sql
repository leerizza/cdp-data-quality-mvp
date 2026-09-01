-- NOTE: there is deliberately no "CREATE SCHEMA cdp" here.
-- DuckDB names the attached database after the file, so connecting to
-- database/cdp.duckdb makes "cdp" the *database* name and every
-- cdp.<table> reference below resolves to cdp.main.<table>.
-- Creating a real schema named "cdp" would shadow that and strand the
-- existing data in main. Renaming the .duckdb file breaks this too.
CREATE SCHEMA IF NOT EXISTS dq;

CREATE TABLE IF NOT EXISTS dq.dq_rule_master (
    rule_id VARCHAR PRIMARY KEY,
    domain VARCHAR NOT NULL,
    dataset VARCHAR NOT NULL,
    column_name VARCHAR,
    dimension VARCHAR NOT NULL,
    rule_name VARCHAR NOT NULL,
    severity VARCHAR NOT NULL,
    metric_type VARCHAR NOT NULL,
    threshold DOUBLE,
    implementation VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    owner VARCHAR NOT NULL,
    test_sql VARCHAR,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dq.dq_run (
    run_id VARCHAR,
    dataset VARCHAR,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    status VARCHAR,
    total_records INTEGER
);

CREATE TABLE IF NOT EXISTS dq.dq_result (
    run_id VARCHAR,
    rule_id VARCHAR,
    record_id VARCHAR,
    column_name VARCHAR,
    status VARCHAR,
    severity VARCHAR,
    message VARCHAR,
    created_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dq.dq_summary (
    run_id VARCHAR,
    dataset VARCHAR,
    dimension VARCHAR,
    total_records INTEGER,
    failed_records INTEGER,
    pass_rate DOUBLE,
    status VARCHAR,
    rule_id VARCHAR,
    severity VARCHAR,
    threshold DOUBLE,
    metric_type VARCHAR
);

CREATE TABLE IF NOT EXISTS dq.dq_incident (
    incident_id VARCHAR PRIMARY KEY,
    run_id VARCHAR NOT NULL,
    rule_id VARCHAR NOT NULL,
    record_id VARCHAR,
    severity VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    message VARCHAR,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dq.quarantine_customer (
    run_id VARCHAR NOT NULL,
    incident_id VARCHAR,
    rule_id VARCHAR NOT NULL,
    customer_id VARCHAR NOT NULL,
    severity VARCHAR NOT NULL,
    column_name VARCHAR,
    original_value VARCHAR,
    reason VARCHAR,
    status VARCHAR NOT NULL DEFAULT 'QUARANTINED',
    quarantined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cdp.eligible_customer (
    run_id VARCHAR NOT NULL,
    customer_id VARCHAR NOT NULL,
    nik VARCHAR,
    full_name VARCHAR,
    phone VARCHAR,
    email VARCHAR,
    birth_date DATE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cdp.identity_match (
    match_id VARCHAR PRIMARY KEY,
    source_system VARCHAR NOT NULL,
    source_customer_id VARCHAR NOT NULL,
    candidate_golden_id VARCHAR,
    match_type VARCHAR NOT NULL,
    match_score DOUBLE,
    match_status VARCHAR NOT NULL,
    matched_on VARCHAR,
    reason VARCHAR,
    candidate_source_system VARCHAR,
    candidate_source_customer_id VARCHAR,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cdp.identity_match_candidate (
    candidate_id VARCHAR PRIMARY KEY,
    source_system VARCHAR NOT NULL,
    source_customer_id VARCHAR NOT NULL,
    candidate_source_system VARCHAR NOT NULL,
    candidate_source_customer_id VARCHAR NOT NULL,
    nik_match BOOLEAN,
    dob_match BOOLEAN,
    phone_match BOOLEAN,
    email_match BOOLEAN,
    dob_conflict BOOLEAN,
    phone_conflict BOOLEAN,
    email_conflict BOOLEAN,
    score DOUBLE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cdp.identity_resolution_decision (
    decision_id VARCHAR PRIMARY KEY,

    source_system VARCHAR NOT NULL,
    source_customer_id VARCHAR NOT NULL,

    best_candidate_source_system VARCHAR,
    best_candidate_source_customer_id VARCHAR,

    match_score DOUBLE,

    match_status VARCHAR NOT NULL,
    confidence VARCHAR NOT NULL,

    has_conflict BOOLEAN NOT NULL,
    conflict_count INTEGER NOT NULL,

    matched_on VARCHAR,
    reason VARCHAR,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cdp.golden_entity (
    golden_id VARCHAR PRIMARY KEY,
    entity_type VARCHAR NOT NULL,
    entity_status VARCHAR NOT NULL,
    has_conflict BOOLEAN NOT NULL DEFAULT FALSE,
    confidence VARCHAR NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cdp.golden_entity_member (
    golden_id VARCHAR NOT NULL,
    source_system VARCHAR NOT NULL,
    source_customer_id VARCHAR NOT NULL,
    membership_status VARCHAR NOT NULL,
    membership_confidence VARCHAR NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (
        golden_id,
        source_system,
        source_customer_id
    )
);
CREATE TABLE IF NOT EXISTS cdp.golden_customer_attribute (
    golden_id VARCHAR NOT NULL,
    attribute_name VARCHAR NOT NULL,
    attribute_value VARCHAR,

    source_system VARCHAR,
    source_customer_id VARCHAR,

    source_priority INTEGER,

    dq_eligible BOOLEAN,

    selection_reason VARCHAR,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (
        golden_id,
        attribute_name
    )
);

CREATE TABLE IF NOT EXISTS cdp.golden_customer (
    golden_id VARCHAR PRIMARY KEY,

    nik VARCHAR,
    full_name VARCHAR,
    phone VARCHAR,
    email VARCHAR,
    birth_date DATE,

    entity_status VARCHAR,
    confidence VARCHAR,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dq.dq_attribute_result (
    run_id VARCHAR NOT NULL,
    source_system VARCHAR NOT NULL,
    source_customer_id VARCHAR NOT NULL,
    attribute_name VARCHAR NOT NULL,

    status VARCHAR NOT NULL,

    rule_id VARCHAR,
    message VARCHAR,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cdp.golden_quality_score (
    golden_id VARCHAR PRIMARY KEY, 
    total_attributes INTEGER, 
    valid_attributes INTEGER, 
    missing_attributes INTEGER, 
    completeness_score DOUBLE, 
    validity_score DOUBLE, 
    identity_confidence VARCHAR, 
    has_conflict BOOLEAN, 
    quality_score DOUBLE, 
    quality_status VARCHAR, 
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cdp.cross_source_consistency (
    consistency_id VARCHAR PRIMARY KEY,

    golden_id VARCHAR NOT NULL,

    attribute_name VARCHAR NOT NULL,

    distinct_value_count INTEGER NOT NULL,

    consistency_status VARCHAR NOT NULL,

    source_values VARCHAR,

    severity VARCHAR,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cdp.review_queue (
    review_id VARCHAR PRIMARY KEY,

    issue_type VARCHAR NOT NULL,

    golden_id VARCHAR,

    source_system VARCHAR,
    source_customer_id VARCHAR,

    attribute_name VARCHAR,

    severity VARCHAR NOT NULL,

    reason VARCHAR NOT NULL,

    status VARCHAR NOT NULL,

    assigned_to VARCHAR,

    resolution VARCHAR,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    resolved_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cdp.identity_resolution_action (
    action_id VARCHAR PRIMARY KEY,
    review_id VARCHAR NOT NULL,
    action_type VARCHAR NOT NULL,
    source_system VARCHAR,
    source_customer_id VARCHAR,
    golden_id VARCHAR,
    performed_by VARCHAR NOT NULL,
    reason VARCHAR,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================
-- CDP-level quality: one score per pipeline run, plus the
-- dimension breakdown behind it and the gate verdict it drove.
-- Rows accumulate per run so trend analysis has something to read.
-- =============================================================

CREATE TABLE IF NOT EXISTS cdp.cdp_quality_score (
    run_id VARCHAR PRIMARY KEY,
    overall_score DOUBLE,
    overall_status VARCHAR NOT NULL,
    assessed_entities INTEGER NOT NULL,
    total_entities INTEGER NOT NULL,
    scored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cdp.cdp_quality_dimension (
    run_id VARCHAR NOT NULL,
    dimension VARCHAR NOT NULL,
    score DOUBLE,
    weight DOUBLE NOT NULL,
    contribution DOUBLE,
    detail VARCHAR,
    PRIMARY KEY (run_id, dimension)
);

CREATE TABLE IF NOT EXISTS cdp.cdp_quality_gate (
    run_id VARCHAR PRIMARY KEY,
    gate_status VARCHAR NOT NULL,
    overall_score DOUBLE,
    blocking_reasons VARCHAR,
    warning_reasons VARCHAR,
    evaluated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================
-- Golden ID registry (persistent identity).
--
-- identity_clustering rebuilds clusters from scratch on every run. Without
-- a registry the golden ids were positional (G000001, G000002, ... in sort
-- order), so any membership change renumbered unrelated entities and broke
-- every downstream reference - including cdp.identity_resolution_action,
-- which stores the golden_id a steward approved.
--
-- The registry maps a cluster to a stable golden_id:
--   cluster_signature  sha256 of the sorted member keys
--   is_active          FALSE once a cluster no longer exists; rows are
--                      retired, never deleted, and their numbers are
--                      never handed out again.
-- =============================================================

CREATE TABLE IF NOT EXISTS cdp.golden_entity_identity (
    golden_id VARCHAR PRIMARY KEY,
    cluster_signature VARCHAR NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_golden_entity_identity_signature
    ON cdp.golden_entity_identity (cluster_signature);

-- =============================================================
-- Identity resolution metrics, one row per run.
--
-- Replaces the single resolved/total ratio the CDP score used, which
-- counted a record sitting in a REVIEW entity as fully resolved. The
-- pair-level rates share a denominator (every evaluated pair) so they
-- can be read together; the record-level rates share theirs (every
-- source record).
-- =============================================================

CREATE TABLE IF NOT EXISTS cdp.identity_metrics (
    run_id VARCHAR PRIMARY KEY,

    total_pairs INTEGER NOT NULL,
    match_rate DOUBLE,
    possible_match_rate DOUBLE,
    conflict_rate DOUBLE,

    total_source_records INTEGER NOT NULL,
    confirmed_records INTEGER NOT NULL,
    review_records INTEGER NOT NULL,
    unresolved_records INTEGER NOT NULL,
    unresolved_rate DOUBLE,

    golden_entity_count INTEGER NOT NULL,
    avg_cluster_size DOUBLE,

    resolution_health DOUBLE,

    computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================
-- Per-run snapshot for history and trend.
--
-- Golden-layer tables hold only the current state (no run_id), so a
-- past run's entity counts cannot be reconstructed later. Each run
-- therefore records its own snapshot as it happens; rows are never
-- rewritten for earlier runs.
-- =============================================================

CREATE TABLE IF NOT EXISTS dq.dq_run_history (
    run_id VARCHAR PRIMARY KEY,
    started_at TIMESTAMP,

    total_records INTEGER,
    quarantined_records INTEGER,
    source_pass_rate DOUBLE,
    critical_failed_rules INTEGER,
    high_failed_rules INTEGER,

    golden_entities INTEGER,
    golden_customers INTEGER,
    assessed_entities INTEGER,

    open_reviews INTEGER,
    high_open_reviews INTEGER,

    cdp_overall_score DOUBLE,
    cdp_gate_status VARCHAR,

    snapshot_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================
-- Golden record lineage: one row per surviving attribute, carrying
-- the whole chain from source record through DQ, identity decision
-- and survivorship, so any golden value can be explained.
-- =============================================================

CREATE TABLE IF NOT EXISTS cdp.golden_lineage (
    golden_id VARCHAR NOT NULL,
    attribute_name VARCHAR NOT NULL,
    attribute_value VARCHAR,

    source_system VARCHAR,
    source_customer_id VARCHAR,
    source_priority INTEGER,

    dq_eligible BOOLEAN,
    attribute_dq_status VARCHAR,
    attribute_dq_message VARCHAR,

    membership_status VARCHAR,
    membership_confidence VARCHAR,
    resolution_action VARCHAR,
    resolved_by VARCHAR,

    entity_status VARCHAR,
    entity_confidence VARCHAR,

    selection_reason VARCHAR,
    run_id VARCHAR,
    built_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (golden_id, attribute_name)
);

-- =============================================================
-- Migrations
--
-- CREATE TABLE IF NOT EXISTS leaves pre-existing tables untouched,
-- so columns added after a database was first created must be
-- backfilled here. Safe to re-run.
-- =============================================================

ALTER TABLE dq.dq_summary ADD COLUMN IF NOT EXISTS rule_id VARCHAR;
ALTER TABLE dq.dq_summary ADD COLUMN IF NOT EXISTS severity VARCHAR;
ALTER TABLE dq.dq_summary ADD COLUMN IF NOT EXISTS threshold DOUBLE;
ALTER TABLE dq.dq_summary ADD COLUMN IF NOT EXISTS metric_type VARCHAR;

ALTER TABLE cdp.identity_match ADD COLUMN IF NOT EXISTS candidate_source_system VARCHAR;
ALTER TABLE cdp.identity_match ADD COLUMN IF NOT EXISTS candidate_source_customer_id VARCHAR;