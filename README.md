# CDP Data Quality MVP

A proof of concept exploring how data quality applies to a Customer Data
Platform. It goes past source-level checks: DQ verdicts are used as
*decision input* for identity resolution, survivorship and publication,
not just as a reporting layer.

```
Source CSV
  -> Data contract        shape check before anything is loaded
  -> Source DQ            rule engine, quarantine, gate
  -> Unified customer     dbt models across CRM / LOS / MOBILE
  -> Identity Resolution   candidates -> decisions -> clustering
  -> Golden Entity
  -> Attribute DQ          per-attribute PASS/FAIL
  -> Survivorship          DQ eligibility first, then source priority
  -> Golden Customer       with full attribute provenance
  -> Cross-source consistency
  -> Review / Remediation  human decisions, audited
  -> CDP Quality Score + Gate
```

## Stack

Python 3.11 - DuckDB - dbt 1.12 (dbt-duckdb) - CSV seeds. Runs entirely
locally; no services required.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

dbt reads the `cdp_dq` profile from `~/.dbt/profiles.yml`:

```yaml
cdp_dq:
  target: dev
  outputs:
    dev:
      type: duckdb
      path: <repo>/database/cdp.duckdb
      threads: 1
```

> The database file **must** stay at `database/cdp.duckdb`. DuckDB names the
> attached database after the file, so every `cdp.<table>` reference in the
> engine resolves through that name. Renaming it breaks the pipeline.

## Running

```powershell
python main.py                 # full pipeline
python main.py --list          # show every stage and step
python main.py --stage dq      # one stage
python main.py --from identity # this stage onwards
python main.py --strict        # stop when a quality gate reports BLOCKED
python main.py --generate      # regenerate source CSVs first
python main.py --dry-run
```

The database is not committed (it is a build artifact). On a fresh clone:

```powershell
python main.py                                          # builds everything
python dq_engine\review\resolve_identity.py --import-actions   # restore steward decisions
python main.py --from identity                          # apply them
```

## Layout

```
main.py                  orchestrator - stage order, gates, CLI
dq_engine/
  sql/                   schema DDL and migrations
  source_dq/             data contracts, rule registry, execution, incidents,
                         quarantine, summary, source gate, attribute DQ
  identity/              candidate generation, decision, ranking, clustering
  golden/                survivorship, cross-source consistency, golden score
  review/                review queue, steward resolution workflow
  scoring/               overall CDP score and CDP gate
  reporting/             run history, lineage, dashboard
  tools/                 source data generation, profiling
dbt/cdp_dq/              staging and unified customer models
metadata/                data contracts, rule registry, steward audit export
data/                    generated source CSVs
reports/                 generated dashboard
```

Every script is a standalone entry point against the same DuckDB file -
nothing imports anything else, so `main.py` owns the ordering. The
subfolders mirror the pipeline stages.

## Data contracts

Every rule in the registry asks a question about the contents of a row,
and none of them survives the column it names disappearing. When a source
system drops or renames a column, `dbt seed` loads the file anyway and the
staging model dies on a binder error - so the run is over before the DQ
engine starts. No verdict, no incident, nothing for the gate to block on.
A stack trace is not a quality signal.

`metadata/data_contract.csv` declares the agreed *shape* of each file -
columns, types, which are required, which may be null. The validator runs
before anything is loaded and reports `FILE_MISSING`, `MISSING_COLUMN`,
`NEW_COLUMN`, `TYPE_MISMATCH` or `NULL_VIOLATION` into
`dq.contract_violation`.

```powershell
python main.py --stage contract
```

The split from the rule registry is deliberate: **a contract describes
structure, a rule describes content**. Nullability is therefore declared
only on key columns - a null email is `DQ-CUS-007`'s business, and putting
it in both places would raise one issue twice under two names.

A missing required column is always CRITICAL, whatever severity the
contract gives it. Severity grades how much an attribute's *content*
matters; a column that is not there is a different question, because every
model naming it fails to compile.

## Quality gates

Three gates, deliberately different questions:

- **Contract gate** (`contract_validator.py`) - is the file still the shape we agreed on?
- **Source gate** (`dq_quality_gate.py`) - are raw records clean enough to load?
- **CDP gate** (`cdp_quality_gate.py`) - is the golden layer fit to publish?

`CRITICAL` severity is **zero tolerance**: one affected record blocks,
whatever the rule's threshold says. Thresholds express tolerable drift;
a null NIK or a duplicate customer id is not something to average away.
The CDP gate's verdict is stored in `cdp.cdp_quality_gate` so the decision
is auditable, and it exits non-zero when BLOCKED.

## Review and remediation

Reviews are raised automatically and resolved explicitly - never
auto-approved:

```powershell
python dq_engine\review\resolve_identity.py --list
python dq_engine\review\resolve_identity.py --source MOBILE:MOB002 --approve `
    --golden-id G000002 --reason "matching phone, email and birth date"
python main.py --from identity
```

Decisions land in `cdp.identity_resolution_action`, which clustering reads
as a trusted edge. That table is the single source of truth for identity
decisions - membership is always derived from it, never written directly.
Audit history is never deleted.

## Explainability

```powershell
python dq_engine\reporting\lineage_builder.py --explain G000002
```

Shows each surviving attribute, the source record it came from, its DQ
verdict, and what lost - e.g. `MOB002.nik -> FAIL (NULL_OR_EMPTY)` is why
the golden NIK comes from CRM002 while the phone still comes from MOB002.

## Dashboard

`python main.py --stage report` writes a self-contained page to
`reports/cdp_dq_dashboard.html`. Open it directly in a browser.

## Status

Done: data contracts, source DQ, attribute DQ, unified customer, identity resolution,
golden entity, survivorship with provenance, cross-source consistency,
review queue and resolution workflow, golden quality score, overall CDP
quality score, CDP quality gate, run history and trend, lineage,
dashboard.

Not started: the feature layer / feature store DQ, which is deliberately
out of scope until the CDP DQ MVP is complete.
