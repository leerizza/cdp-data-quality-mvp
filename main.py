"""CDP Data Quality MVP - pipeline orchestrator.

The dq_engine scripts and the dbt models are each standalone entry points
that talk to the same DuckDB file. Nothing imports anything else, so the
run order below is the only thing that makes the pipeline coherent.

Usage:
    python main.py                     # stage -> dq -> curate -> identity -> golden -> review
    python main.py --list              # show every stage and step
    python main.py --stage dq          # run one stage (repeatable)
    python main.py --from identity     # run this stage and everything after it
    python main.py --generate          # also regenerate the source CSVs first
    python main.py --test              # also run dbt test at the end
    python main.py --strict            # abort if the DQ quality gate reports BLOCKED
    python main.py --dry-run           # print what would run, touch nothing

Note: DuckDB names the attached database after the file, so the database
must stay at database/cdp.duckdb. Renaming it breaks every cdp.<table>
reference and every dbt-generated view. See dq_engine/sql/init_dq.sql.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
DQ_ENGINE = PROJECT_ROOT / "dq_engine"
DBT_DIR = PROJECT_ROOT / "dbt" / "cdp_dq"
DB_PATH = PROJECT_ROOT / "database" / "cdp.duckdb"
DATA_DIR = PROJECT_ROOT / "data"
SEED_DIR = DBT_DIR / "seeds"

SEED_FILES = [
    "customer.csv",
    "crm_customer.csv",
    "los_customer.csv",
    "mobile_customer.csv",
]


@dataclass
class Step:
    """One unit of work in the pipeline."""

    name: str
    kind: str  # "py" | "sql" | "dbt" | "sync"
    target: str = ""
    args: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class Stage:
    name: str
    description: str
    steps: list[Step]


STAGES: list[Stage] = [
    Stage(
        "generate",
        "Regenerate the dummy source CSVs and copy them into dbt seeds",
        [
            Step("generate multi-source CSVs", "py", "tools/generate_multi_source_v2.py"),
            Step("sync data/ -> dbt seeds/", "sync"),
        ],
    ),
    Stage(
        "stage",
        "Load seeds and build the staging + unified models",
        [
            Step("dbt seed", "dbt", args=["seed"]),
            Step(
                "dbt run (staging + unified)",
                "dbt",
                args=[
                    "run",
                    "--select",
                    "stg_customer",
                    "stg_crm_customer",
                    "stg_los_customer",
                    "stg_mobile_customer",
                    "customer_unified",
                ],
            ),
        ],
    ),
    Stage(
        "dq",
        "Run the rule-based DQ engine over stg_customer",
        [
            Step("init DQ schema", "sql", "sql/init_dq.sql"),
            Step("load rules", "py", "source_dq/load_rules.py"),
            Step("execute rules", "py", "source_dq/dq_executor.py"),
            Step("build incidents", "py", "source_dq/dq_incident_builder.py"),
            Step("build quarantine", "py", "source_dq/quarantine_builder.py"),
            Step("build summary", "py", "source_dq/dq_summary_builder.py"),
            Step(
                "quality gate",
                "py",
                "source_dq/dq_quality_gate.py",
                note="reports only; use --strict to make BLOCKED abort the run",
            ),
        ],
    ),
    Stage(
        "curate",
        "Build the DQ-filtered customer models (depends on quarantine)",
        [
            Step(
                "dbt run (eligible + gold)",
                "dbt",
                args=["run", "--select", "eligible_customer", "gold_customer"],
            ),
        ],
    ),
    Stage(
        "identity",
        "Match, decide and cluster source records into golden entities",
        [
            Step("generate candidates", "py", "identity/identity_candidate_generator.py"),
            Step("decide matches", "py", "identity/identity_decision.py"),
            Step("rank candidates", "py", "identity/identity_ranker.py"),
            Step("cluster into golden entities", "py", "identity/identity_clustering.py"),
            Step("identity metrics", "py", "identity/identity_metrics.py"),
        ],
    ),
    Stage(
        "golden",
        "Score attributes, pick surviving values and grade the golden records",
        [
            Step("attribute-level DQ", "py", "source_dq/attribute_dq.py"),
            Step("survivorship", "py", "golden/survivorship_engine.py"),
            Step("cross-source consistency", "py", "golden/cross_source_consistency.py"),
            Step("golden quality score", "py", "golden/golden_quality_score.py"),
        ],
    ),
    Stage(
        "review",
        "Build the human review queue from unresolved conflicts",
        [
            Step("build review queue", "py", "review/review_queue_builder.py"),
        ],
    ),
    Stage(
        "score",
        "Score the CDP as a whole and decide whether it can be published",
        [
            Step("CDP quality score", "py", "scoring/cdp_quality_score.py"),
            Step(
                "CDP quality gate",
                "py",
                "scoring/cdp_quality_gate.py",
                note="exits non-zero when BLOCKED; verdict is stored in cdp.cdp_quality_gate",
            ),
        ],
    ),
    Stage(
        "lineage",
        "Trace every golden value back to the decisions that produced it",
        [
            Step("build golden lineage", "py", "reporting/lineage_builder.py"),
        ],
    ),
    Stage(
        "history",
        "Snapshot this run and compare it with the previous one",
        [
            Step("run history + trend", "py", "reporting/dq_history.py"),
        ],
    ),
    Stage(
        "report",
        "Render the dashboard to reports/cdp_dq_dashboard.html",
        [
            Step("build dashboard", "py", "reporting/build_dashboard.py"),
        ],
    ),
    Stage(
        "test",
        "Run the dbt tests",
        [
            Step("dbt test", "dbt", args=["test"]),
        ],
    ),
]

# Gates report by default and only halt the run under --strict.
GATE_STEPS = {"source_dq/dq_quality_gate.py", "scoring/cdp_quality_gate.py"}

# Stages that only run when explicitly asked for.
OPT_IN = {"generate", "test"}
DEFAULT_STAGES = [s.name for s in STAGES if s.name not in OPT_IN]

STAGES_BY_NAME = {s.name: s for s in STAGES}


def child_env() -> dict[str, str]:
    """Environment for child processes.

    The scripts print DuckDB result tables, which use box-drawing
    characters. On a cp1252 Windows console that raises
    UnicodeEncodeError, so force UTF-8 for every child.
    """
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_python(script: str, capture: bool) -> tuple[int, str]:
    return _run([sys.executable, script], cwd=DQ_ENGINE, capture=capture)


def run_dbt(args: list[str], capture: bool) -> tuple[int, str]:
    return _run([_dbt_executable(), *args], cwd=DBT_DIR, capture=capture)


def _dbt_executable() -> str:
    """Prefer the dbt next to the running interpreter, else whatever is on PATH."""
    candidate = Path(sys.executable).parent / ("dbt.exe" if os.name == "nt" else "dbt")
    return str(candidate) if candidate.exists() else "dbt"


def _run(cmd: list[str], cwd: Path, capture: bool) -> tuple[int, str]:
    if capture:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env=child_env(),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        print(proc.stdout, end="" if proc.stdout.endswith("\n") else "\n")
        return proc.returncode, proc.stdout

    proc = subprocess.run(cmd, cwd=cwd, env=child_env())
    return proc.returncode, ""


def run_sql(filename: str) -> tuple[int, str]:
    import duckdb

    sql = (DQ_ENGINE / filename).read_text(encoding="utf-8")
    conn = duckdb.connect(str(DB_PATH))
    try:
        conn.execute(sql)
    finally:
        conn.close()
    return 0, ""


def sync_seeds() -> tuple[int, str]:
    """Copy the generated CSVs into dbt's seed directory.

    data/ and dbt/cdp_dq/seeds/ hold separate copies of the same files;
    without this the models keep building from stale seeds.
    """
    copied = []
    for name in SEED_FILES:
        src = DATA_DIR / name
        if not src.exists():
            print(f"    skip {name} (not in data/)")
            continue
        shutil.copyfile(src, SEED_DIR / name)
        copied.append(name)
    print(f"    synced {len(copied)} seed file(s): {', '.join(copied)}")
    return 0, ""


def execute_step(step: Step, strict: bool) -> None:
    """Run one step, raising RuntimeError if it fails.

    Both gates report by default and only stop the run under --strict.
    The source gate prints its verdict without setting an exit code, so
    it has to be read out of its output; the CDP gate signals through
    its exit code.
    """
    is_gate = step.target in GATE_STEPS
    # Reading the source gate's verdict means capturing its output.
    capture = strict and step.target == "source_dq/dq_quality_gate.py"

    if step.kind == "py":
        code, out = run_python(step.target, capture)
    elif step.kind == "dbt":
        code, out = run_dbt(step.args, capture=False)
    elif step.kind == "sql":
        code, out = run_sql(step.target)
    elif step.kind == "sync":
        code, out = sync_seeds()
    else:
        raise RuntimeError(f"unknown step kind: {step.kind}")

    if code != 0:
        if not is_gate:
            raise RuntimeError(f"{step.name} exited with code {code}")
        if strict:
            raise RuntimeError(f"{step.name} reported BLOCKED (--strict)")
        print(f"--- {step.name} reported BLOCKED (continuing; use --strict to stop)")

    if capture and "BLOCKED" in out:
        raise RuntimeError(
            "quality gate reported BLOCKED (--strict); "
            "downstream models would be built on failing data"
        )


def describe(step: Step) -> str:
    if step.kind == "py":
        return f"python dq_engine/{step.target}"
    if step.kind == "dbt":
        return "dbt " + " ".join(step.args)
    if step.kind == "sql":
        return f"duckdb < dq_engine/{step.target}"
    return "copy data/*.csv -> dbt/cdp_dq/seeds/"


def print_plan() -> None:
    for stage in STAGES:
        tag = "  (opt-in)" if stage.name in OPT_IN else ""
        print(f"\n{stage.name}{tag}\n  {stage.description}")
        for step in stage.steps:
            print(f"    - {step.name:<32} {describe(step)}")
            if step.note:
                print(f"      note: {step.note}")
    print(f"\nDefault run: {' -> '.join(DEFAULT_STAGES)}\n")


def resolve_stages(args: argparse.Namespace) -> list[str]:
    if args.stage:
        unknown = [s for s in args.stage if s not in STAGES_BY_NAME]
        if unknown:
            raise SystemExit(f"unknown stage(s): {', '.join(unknown)}")
        return list(args.stage)

    selected = list(DEFAULT_STAGES)

    if args.start_from:
        if args.start_from not in STAGES_BY_NAME:
            raise SystemExit(f"unknown stage: {args.start_from}")
        if args.start_from in selected:
            selected = selected[selected.index(args.start_from):]
        else:
            selected = [args.start_from]

    if args.generate:
        selected = ["generate"] + selected
    if args.test:
        selected = selected + ["test"]

    return selected


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the CDP data quality pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--list", action="store_true", help="show stages and exit")
    parser.add_argument(
        "--stage",
        action="append",
        metavar="NAME",
        help="run only this stage (repeatable)",
    )
    parser.add_argument(
        "--from",
        dest="start_from",
        metavar="NAME",
        help="start at this stage and run everything after it",
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help="regenerate the source CSVs before staging",
    )
    parser.add_argument("--test", action="store_true", help="run dbt test at the end")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="abort when the DQ quality gate reports BLOCKED",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the steps without running them",
    )
    args = parser.parse_args()

    if args.list:
        print_plan()
        return 0

    if not DB_PATH.exists() and not args.dry_run:
        print(f"! database not found: {DB_PATH}", file=sys.stderr)
        print("  the dbt profile and every dq_engine script expect it there.", file=sys.stderr)
        return 1

    stage_names = resolve_stages(args)
    started = time.time()
    done: list[tuple[str, float]] = []

    for stage_name in stage_names:
        stage = STAGES_BY_NAME[stage_name]
        print(f"\n{'=' * 66}")
        print(f"STAGE: {stage.name} - {stage.description}")
        print("=" * 66)

        for step in stage.steps:
            if args.dry_run:
                print(f"  [dry-run] {step.name:<32} {describe(step)}")
                continue

            print(f"\n--- {step.name} ({describe(step)})")
            step_started = time.time()
            try:
                execute_step(step, args.strict)
            except Exception as exc:
                print(f"\n! FAILED at stage '{stage.name}', step '{step.name}'", file=sys.stderr)
                print(f"  {exc}", file=sys.stderr)
                if done:
                    print(f"  completed before failure: {', '.join(n for n, _ in done)}", file=sys.stderr)
                return 1
            elapsed = time.time() - step_started
            done.append((f"{stage.name}/{step.name}", elapsed))
            print(f"--- ok ({elapsed:.1f}s)")

    if args.dry_run:
        print("\ndry run complete, nothing was executed.")
        return 0

    print(f"\n{'=' * 66}")
    print(f"PIPELINE OK - {len(done)} steps in {time.time() - started:.1f}s")
    print("=" * 66)
    for name, elapsed in done:
        print(f"  {elapsed:6.1f}s  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
