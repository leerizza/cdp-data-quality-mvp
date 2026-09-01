"""Render the CDP data quality dashboard as a standalone HTML file.

Reads the current state straight out of DuckDB and writes a single
self-contained page to reports/. No server, no build step - open the
file in a browser. Re-run it after a pipeline run to refresh.
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "database" / "cdp.duckdb"
OUT_DIR = PROJECT_ROOT / "reports"
OUT_FILE = OUT_DIR / "cdp_dq_dashboard.html"

STATUS_CLASS = {
    "PASSED": "ok",
    "EXCELLENT": "ok",
    "GOOD": "ok",
    "CONFIRMED": "ok",
    "PASS": "ok",
    "ACTIVE": "ok",
    "WARNING": "warn",
    "REVIEW": "warn",
    "VARIATION": "warn",
    "NOT_ASSESSED": "muted",
    "BLOCKED": "bad",
    "FAIL": "bad",
    "CONFLICT": "bad",
    "POOR": "bad",
}


def esc(value) -> str:
    return html.escape("" if value is None else str(value))


def pill(value) -> str:
    cls = STATUS_CLASS.get(str(value), "muted")
    return f'<span class="pill {cls}">{esc(value)}</span>'


def table(conn, sql, headers, params=None, pill_cols=()):
    rows = conn.execute(sql, params or []).fetchall()
    if not rows:
        return '<p class="empty">No rows.</p>'

    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = []
    for row in rows:
        cells = []
        for i, cell in enumerate(row):
            if isinstance(cell, float):
                cell = round(cell, 2)
            cells.append(f"<td>{pill(cell) if i in pill_cols else esc(cell)}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")

    return (
        '<div class="scroll"><table><thead><tr>'
        + head
        + "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table></div>"
    )


def scalar(conn, sql, params=None, default=None):
    row = conn.execute(sql, params or []).fetchone()
    return row[0] if row and row[0] is not None else default


def build(conn) -> str:
    run_id = scalar(conn, "SELECT run_id FROM dq.dq_run ORDER BY started_at DESC LIMIT 1")

    overall = scalar(
        conn, "SELECT overall_score FROM cdp.cdp_quality_score WHERE run_id = ?", [run_id]
    )
    gate = scalar(
        conn, "SELECT gate_status FROM cdp.cdp_quality_gate WHERE run_id = ?", [run_id], "-"
    )
    blocking = scalar(
        conn, "SELECT blocking_reasons FROM cdp.cdp_quality_gate WHERE run_id = ?", [run_id]
    )
    warnings = scalar(
        conn, "SELECT warning_reasons FROM cdp.cdp_quality_gate WHERE run_id = ?", [run_id]
    )

    entities = scalar(conn, "SELECT COUNT(*) FROM cdp.golden_entity", default=0)
    customers = scalar(conn, "SELECT COUNT(*) FROM cdp.golden_customer", default=0)
    quarantined = scalar(
        conn,
        "SELECT COUNT(DISTINCT customer_id) FROM dq.quarantine_customer WHERE run_id = ?",
        [run_id],
        0,
    )
    open_reviews = scalar(
        conn, "SELECT COUNT(*) FROM cdp.review_queue WHERE status = 'OPEN'", default=0
    )

    score_text = f"{overall:.1f}" if overall is not None else "n/a"

    banner = ""
    if blocking:
        banner = f'<div class="banner bad"><strong>BLOCKED</strong> {esc(blocking)}</div>'
    elif warnings:
        banner = f'<div class="banner warn"><strong>WARNING</strong> {esc(warnings)}</div>'

    return f"""<title>CDP Data Quality</title>
<style>
  :root {{
    --bg: #f7f7f8; --panel: #ffffff; --ink: #18181b; --muted-ink: #6b7280;
    --line: #e4e4e7; --ok: #15803d; --ok-bg: #dcfce7; --warn: #a16207;
    --warn-bg: #fef9c3; --bad: #b91c1c; --bad-bg: #fee2e2; --accent: #1d4ed8;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --bg: #0b0b0e; --panel: #17171b; --ink: #f4f4f5; --muted-ink: #a1a1aa;
      --line: #2a2a31; --ok: #4ade80; --ok-bg: #14321f; --warn: #fbbf24;
      --warn-bg: #35290a; --bad: #f87171; --bad-bg: #3b1414; --accent: #93b4fd;
    }}
  }}
  :root[data-theme="dark"] {{
    --bg: #0b0b0e; --panel: #17171b; --ink: #f4f4f5; --muted-ink: #a1a1aa;
    --line: #2a2a31; --ok: #4ade80; --ok-bg: #14321f; --warn: #fbbf24;
    --warn-bg: #35290a; --bad: #f87171; --bad-bg: #3b1414; --accent: #93b4fd;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 32px 20px 64px; background: var(--bg); color: var(--ink);
    font: 15px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  }}
  .wrap {{ max-width: 1100px; margin: 0 auto; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; letter-spacing: -0.01em; }}
  h2 {{ font-size: 15px; margin: 34px 0 10px; text-transform: uppercase;
       letter-spacing: .08em; color: var(--muted-ink); }}
  .sub {{ color: var(--muted-ink); font-size: 13px; margin-bottom: 22px; }}
  .cards {{ display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }}
  .card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; }}
  .card .label {{ font-size: 11px; text-transform: uppercase; letter-spacing: .07em; color: var(--muted-ink); }}
  .card .value {{ font-size: 26px; font-weight: 600; margin-top: 6px; letter-spacing: -0.02em; }}
  .banner {{ border-radius: 10px; padding: 12px 16px; margin: 18px 0 0; font-size: 14px; border: 1px solid; }}
  .banner.bad {{ background: var(--bad-bg); border-color: var(--bad); color: var(--bad); }}
  .banner.warn {{ background: var(--warn-bg); border-color: var(--warn); color: var(--warn); }}
  .scroll {{ overflow-x: auto; border: 1px solid var(--line); border-radius: 10px; background: var(--panel); }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13.5px; }}
  th, td {{ text-align: left; padding: 9px 13px; border-bottom: 1px solid var(--line); white-space: nowrap; }}
  th {{ font-size: 11px; text-transform: uppercase; letter-spacing: .06em;
       color: var(--muted-ink); font-weight: 600; }}
  tbody tr:last-child td {{ border-bottom: none; }}
  .pill {{ display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: 12px; font-weight: 600; }}
  .pill.ok {{ background: var(--ok-bg); color: var(--ok); }}
  .pill.warn {{ background: var(--warn-bg); color: var(--warn); }}
  .pill.bad {{ background: var(--bad-bg); color: var(--bad); }}
  .pill.muted {{ background: var(--line); color: var(--muted-ink); }}
  .empty {{ color: var(--muted-ink); font-style: italic; }}
  footer {{ margin-top: 40px; color: var(--muted-ink); font-size: 12px;
            border-top: 1px solid var(--line); padding-top: 14px; }}
</style>

<div class="wrap">
  <h1>CDP Data Quality</h1>
  <div class="sub">Run <code>{esc(run_id)}</code> &middot; generated {datetime.now():%Y-%m-%d %H:%M}</div>

  <div class="cards">
    <div class="card"><div class="label">CDP Score</div><div class="value">{esc(score_text)}</div></div>
    <div class="card"><div class="label">Gate</div><div class="value">{pill(gate)}</div></div>
    <div class="card"><div class="label">Golden Entities</div><div class="value">{entities}</div></div>
    <div class="card"><div class="label">Golden Customers</div><div class="value">{customers}</div></div>
    <div class="card"><div class="label">Quarantined</div><div class="value">{quarantined}</div></div>
    <div class="card"><div class="label">Open Reviews</div><div class="value">{open_reviews}</div></div>
  </div>

  {banner}

  <h2>Score breakdown</h2>
  {table(conn, '''
      SELECT dimension, ROUND(score,1), weight, ROUND(contribution,1), detail
      FROM cdp.cdp_quality_dimension WHERE run_id = ?
      ORDER BY weight DESC, dimension
  ''', ["Dimension", "Score", "Weight", "Contribution", "Detail"], [run_id])}

  <h2>Source DQ rules</h2>
  {table(conn, '''
      SELECT rule_id, severity, status, failed_records, threshold, metric_type
      FROM dq.dq_summary WHERE run_id = ? ORDER BY rule_id
  ''', ["Rule", "Severity", "Status", "Failed", "Threshold", "Metric"], [run_id], pill_cols={2})}

  <h2>Identity resolution</h2>
  {table(conn, '''
      SELECT total_pairs, ROUND(match_rate,1), ROUND(possible_match_rate,1),
             ROUND(conflict_rate,1), confirmed_records, review_records,
             unresolved_records, golden_entity_count, ROUND(avg_cluster_size,2),
             ROUND(resolution_health,1)
      FROM cdp.identity_metrics WHERE run_id = ?
  ''', ["Pairs", "Match %", "Possible %", "Conflict %", "Confirmed",
        "In review", "Unresolved", "Entities", "Avg cluster", "Health"],
       [run_id])}

  <h2>Golden entities</h2>
  {table(conn, '''
      SELECT e.golden_id, e.entity_status, e.confidence, e.has_conflict,
             s.quality_status, ROUND(s.quality_score,1)
      FROM cdp.golden_entity e
      LEFT JOIN cdp.golden_quality_score s ON s.golden_id = e.golden_id
      ORDER BY e.golden_id
  ''', ["Golden ID", "Status", "Confidence", "Conflict", "Quality", "Score"],
       pill_cols={1, 4})}

  <h2>Golden record lineage</h2>
  {table(conn, '''
      SELECT golden_id, attribute_name, attribute_value,
             source_system || ':' || source_customer_id,
             attribute_dq_status, resolution_action
      FROM cdp.golden_lineage ORDER BY golden_id, attribute_name
  ''', ["Golden ID", "Attribute", "Value", "Source", "Attr DQ", "Steward"],
       pill_cols={4})}

  <h2>Review cases</h2>
  {table(conn, '''
      SELECT subject_type, subject_key, severity, status,
             evidence_count, issue_types, summary
      FROM cdp.review_case ORDER BY status, severity, subject_key
  ''', ["Subject", "Key", "Severity", "Status", "Evidence", "Types", "Summary"],
       pill_cols={3})}

  <h2>Open review queue</h2>
  {table(conn, '''
      SELECT issue_type, severity, golden_id,
             COALESCE(source_system || ':' || source_customer_id, '-'),
             attribute_name, reason
      FROM cdp.review_queue WHERE status = 'OPEN'
      ORDER BY severity, issue_type
  ''', ["Issue", "Severity", "Golden ID", "Source", "Attribute", "Reason"])}

  <h2>Cross-source consistency</h2>
  {table(conn, '''
      SELECT golden_id, attribute_name, consistency_status, distinct_value_count, source_values
      FROM cdp.cross_source_consistency
      WHERE consistency_status <> 'CONSISTENT'
      ORDER BY consistency_status, golden_id
  ''', ["Golden ID", "Attribute", "Status", "Distinct", "Values"], pill_cols={2})}

  <h2>Golden profile history</h2>
  {table(conn, '''
      SELECT golden_id, full_name, phone, email, entity_status,
             CASE WHEN is_current THEN 'current' ELSE 'superseded' END,
             change_reason, valid_from, valid_to
      FROM cdp.golden_customer_history
      ORDER BY golden_id, valid_from
  ''', ["Golden ID", "Name", "Phone", "Email", "Entity", "Version",
        "Change", "Valid from", "Valid to"])}

  <h2>Run history</h2>
  {table(conn, '''
      SELECT run_id, ROUND(source_pass_rate,2), quarantined_records,
             critical_failed_rules, open_reviews, ROUND(cdp_overall_score,1), cdp_gate_status
      FROM dq.dq_run_history ORDER BY started_at
  ''', ["Run", "Source pass %", "Quarantined", "Critical", "Reviews", "CDP score", "Gate"],
       pill_cols={6})}

  <h2>Steward decisions</h2>
  {table(conn, '''
      SELECT action_type, source_system || ':' || source_customer_id,
             golden_id, performed_by, reason, created_at
      FROM cdp.identity_resolution_action ORDER BY created_at
  ''', ["Action", "Source", "Golden ID", "By", "Reason", "When"])}

  <footer>
    Generated from <code>database/cdp.duckdb</code> by
    <code>dq_engine/build_dashboard.py</code>. Re-run the pipeline and this
    script to refresh.
  </footer>
</div>
"""


def main() -> int:
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        page = build(conn)
    finally:
        conn.close()

    OUT_DIR.mkdir(exist_ok=True)
    OUT_FILE.write_text(page, encoding="utf-8")
    print(f"\n=== DASHBOARD ===\nwrote {OUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
