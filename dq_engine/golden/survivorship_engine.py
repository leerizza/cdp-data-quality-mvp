from pathlib import Path

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "database" / "cdp.duckdb"

ATTRIBUTES = [
    "nik",
    "full_name",
    "phone",
    "email",
    "birth_date",
]

SURVIVORSHIP_RULE_CSV = PROJECT_ROOT / "metadata" / "survivorship_rule.csv"


def load_source_priority(conn) -> dict[str, dict[str, int]]:
    """Read attribute source priority from metadata.

    Loaded through DuckDB rather than a Python CSV reader so the engine
    keeps its one dependency, and read at run time so editing the CSV
    actually changes which source wins - it used to be a dict in this
    file, and the CSV beside it was decorative.

    Only ACTIVE rows are loaded, so a rule can be retired without being
    deleted from the file.
    """
    if not SURVIVORSHIP_RULE_CSV.exists():
        raise RuntimeError(f"Survivorship rules not found: {SURVIVORSHIP_RULE_CSV}")

    rows = conn.execute(
        """
        SELECT attribute_name, source_system, CAST(priority AS INTEGER)
        FROM read_csv_auto(?)
        WHERE upper(status) = 'ACTIVE'
        ORDER BY attribute_name, priority
        """,
        [str(SURVIVORSHIP_RULE_CSV)],
    ).fetchall()

    if not rows:
        raise RuntimeError(
            f"No ACTIVE survivorship rules in {SURVIVORSHIP_RULE_CSV.name}; "
            "survivorship cannot pick a winning source."
        )

    priority: dict[str, dict[str, int]] = {}
    for attribute_name, source_system, rank in rows:
        priority.setdefault(attribute_name, {})[source_system] = rank

    return priority


def normalize(value):
    if value is None:
        return None

    value = str(value).strip()

    if value == "":
        return None

    return value


def get_dq_status(
    conn,
    source_system,
    source_customer_id,
    attribute,
):
    row = conn.execute(
        """
        SELECT
            status,
            message
        FROM dq.dq_attribute_result
        WHERE source_system = ?
          AND source_customer_id = ?
          AND attribute_name = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        [
            source_system,
            source_customer_id,
            attribute,
        ],
    ).fetchone()

    if row is None:
        return "NO_DQ_RESULT", "No DQ result found"

    return row[0], row[1]


def main():
    conn = duckdb.connect(str(DB_PATH))

    source_priority = load_source_priority(conn)
    print(
        f"Loaded source priority for {len(source_priority)} attribute(s) "
        f"from {SURVIVORSHIP_RULE_CSV.name}"
    )

    entities = conn.execute(
        """
        SELECT
            golden_id,
            confidence
        FROM cdp.golden_entity
        WHERE entity_status = 'ACTIVE'
        ORDER BY golden_id
        """
    ).fetchall()

    if not entities:
        raise RuntimeError(
            "No ACTIVE golden entities found."
        )

    conn.execute(
        """
        DELETE FROM cdp.golden_customer_attribute
        """
    )

    conn.execute(
        """
        DELETE FROM cdp.golden_customer
        """
    )

    for golden_id, confidence in entities:

        members = conn.execute(
            """
            SELECT
                source_system,
                source_customer_id
            FROM cdp.golden_entity_member
            WHERE golden_id = ?
              AND membership_status = 'CONFIRMED'
            ORDER BY source_system, source_customer_id
            """,
            [golden_id],
        ).fetchall()

        golden_values = {}

        for attribute in ATTRIBUTES:

            candidates = []

            for (
                source_system,
                source_customer_id,
            ) in members:

                row = conn.execute(
                    f"""
                    SELECT "{attribute}"
                    FROM main.customer_unified
                    WHERE source_system = ?
                      AND source_customer_id = ?
                    """,
                    [
                        source_system,
                        source_customer_id,
                    ],
                ).fetchone()

                if row is None:
                    continue

                value = normalize(row[0])

                if value is None:
                    continue

                dq_status, dq_message = get_dq_status(
                    conn,
                    source_system,
                    source_customer_id,
                    attribute,
                )

                dq_eligible = (
                    dq_status == "PASS"
                )

                print(
                    f"DQ CHECK | "
                    f"{source_system}/"
                    f"{source_customer_id}/"
                    f"{attribute} | "
                    f"{dq_status} | "
                    f"eligible={dq_eligible}"
                )

                # ============================================
                # CORE RULE:
                # DQ FAIL cannot become Golden Attribute.
                # ============================================

                if not dq_eligible:
                    continue

                priority = source_priority.get(
                    attribute,
                    {},
                ).get(
                    source_system,
                    999,
                )

                candidates.append(
                    {
                        "value": value,
                        "source_system": source_system,
                        "source_customer_id": source_customer_id,
                        "priority": priority,
                        "dq_status": dq_status,
                        "dq_message": dq_message,
                    }
                )

            # ------------------------------------------------
            # No DQ-eligible value available.
            # ------------------------------------------------

            if not candidates:
                golden_values[attribute] = None
                continue

            # ------------------------------------------------
            # Select highest-priority eligible value.
            # ------------------------------------------------

            candidates.sort(
                key=lambda x: x["priority"]
            )

            winner = candidates[0]

            golden_values[attribute] = winner

            reason = (
                f"DQ={winner['dq_status']}; "
                f"source_priority={winner['priority']}; "
                f"selected_highest_priority_"
                f"eligible_source"
            )

            conn.execute(
                """
                INSERT INTO cdp.golden_customer_attribute (
                    golden_id,
                    attribute_name,
                    attribute_value,
                    source_system,
                    source_customer_id,
                    source_priority,
                    dq_eligible,
                    selection_reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    golden_id,
                    attribute,
                    winner["value"],
                    winner["source_system"],
                    winner["source_customer_id"],
                    winner["priority"],
                    True,
                    reason,
                ],
            )

        def get_value(attribute):
            candidate = golden_values.get(attribute)

            if candidate is None:
                return None

            return candidate["value"]

        conn.execute(
            """
            INSERT INTO cdp.golden_customer (
                golden_id,
                nik,
                full_name,
                phone,
                email,
                birth_date,
                entity_status,
                confidence
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [
                golden_id,
                get_value("nik"),
                get_value("full_name"),
                get_value("phone"),
                get_value("email"),
                get_value("birth_date"),
                "ACTIVE",
                confidence,
            ],
        )

    print("\n=== GOLDEN CUSTOMER ===")

    print(
        conn.sql(
            """
            SELECT
                golden_id,
                nik,
                full_name,
                phone,
                email,
                birth_date,
                entity_status,
                confidence
            FROM cdp.golden_customer
            ORDER BY golden_id
            """
        )
    )

    print("\n=== ATTRIBUTE PROVENANCE ===")

    print(
        conn.sql(
            """
            SELECT
                golden_id,
                attribute_name,
                attribute_value,
                source_system,
                source_customer_id,
                source_priority,
                dq_eligible,
                selection_reason
            FROM cdp.golden_customer_attribute
            ORDER BY
                golden_id,
                attribute_name
            """
        )
    )

    conn.close()


if __name__ == "__main__":
    main()