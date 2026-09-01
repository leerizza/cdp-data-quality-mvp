# CDP Data Quality MVP

A proof of concept exploring how data quality applies to a Customer Data
Platform. It goes past source-level checks: DQ verdicts are used as
*decision input* for identity resolution, survivorship and publication,
not just as a reporting layer.

```
Source CSV
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
python dq_engine\resolve_identity.py --import-actions   # restore steward decisions
python main.py --from identity                          # apply them
```

## Layout

| Path | What it holds |
|---|---|
| `main.py` | Pipeline orchestrator (stages, ordering, gates) |
| `dq_engine/` | DQ, identity, survivorship, scoring and reporting scripts |
| `dbt/cdp_dq/` | Staging and unified customer models |
| `metadata/` | Rule registry and the versioned steward audit export |
| `data/` | Generated source CSVs |
| `reports/` | Generated dashboard |

## Quality gates

Two gates, deliberately different questions:

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
python dq_engine\resolve_identity.py --list
python dq_engine\resolve_identity.py --source MOBILE:MOB002 --approve `
    --golden-id G000002 --reason "matching phone, email and birth date"
python main.py --from identity
```

Decisions land in `cdp.identity_resolution_action`, which clustering reads
as a trusted edge. That table is the single source of truth for identity
decisions - membership is always derived from it, never written directly.
Audit history is never deleted.

## Explainability

```powershell
python dq_engine\lineage_builder.py --explain G000002
```

Shows each surviving attribute, the source record it came from, its DQ
verdict, and what lost - e.g. `MOB002.nik -> FAIL (NULL_OR_EMPTY)` is why
the golden NIK comes from CRM002 while the phone still comes from MOB002.

## Dashboard

`python main.py --stage report` writes a self-contained page to
`reports/cdp_dq_dashboard.html`. Open it directly in a browser.

## Status

Done: source DQ, attribute DQ, unified customer, identity resolution,
golden entity, survivorship with provenance, cross-source consistency,
review queue and resolution workflow, golden quality score, overall CDP
quality score, CDP quality gate, run history and trend, lineage,
dashboard.

Not started: the feature layer / feature store DQ, which is deliberately
out of scope until the CDP DQ MVP is complete.
