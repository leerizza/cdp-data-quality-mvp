from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]

SOURCE_FILE = PROJECT_ROOT / "data" / "customer.csv"
OUTPUT_DIR = PROJECT_ROOT / "data"


def clean(value):
    if pd.isna(value):
        return None

    value = str(value).strip()

    if value == "":
        return None

    # Safety net against accidental float serialization.
    if value.endswith(".0") and value[:-2].isdigit():
        value = value[:-2]

    return value


def load_customer_data() -> pd.DataFrame:
    if not SOURCE_FILE.exists():
        raise FileNotFoundError(
            f"Source file not found: {SOURCE_FILE}"
        )

    df = pd.read_csv(
        SOURCE_FILE,
        dtype={
            "customer_id": "string",
            "nik": "string",
            "full_name": "string",
            "phone": "string",
            "email": "string",
            "birth_date": "string",
        },
    )

    required = {
        "customer_id",
        "nik",
        "full_name",
        "phone",
        "email",
        "birth_date",
    }

    missing = required - set(df.columns)

    if missing:
        raise ValueError(
            f"Missing columns: {sorted(missing)}"
        )

    # Normalize string columns explicitly.
    for column in [
        "customer_id",
        "nik",
        "full_name",
        "phone",
        "email",
        "birth_date",
    ]:
        df[column] = df[column].map(clean)

    return df


def get_customer(
    df: pd.DataFrame,
    customer_id: str,
) -> dict:
    rows = df[
        df["customer_id"] == customer_id
    ]

    if len(rows) != 1:
        raise ValueError(
            f"Expected exactly one row for "
            f"{customer_id}, found {len(rows)}"
        )

    row = rows.iloc[0]

    return {
        "customer_id": row["customer_id"],
        "nik": row["nik"],
        "full_name": row["full_name"],
        "phone": row["phone"],
        "email": row["email"],
        "birth_date": row["birth_date"],
    }


def build_source_record(
    source_customer_id: str,
    customer: dict,
) -> dict:
    return {
        "source_customer_id": source_customer_id,
        "nik": customer["nik"],
        "full_name": customer["full_name"],
        "phone": customer["phone"],
        "email": customer["email"],
        "birth_date": customer["birth_date"],
    }


def print_mapping(
    source_name: str,
    source_records: list[dict],
) -> None:
    print(f"\n=== {source_name} MAPPING ===")

    for record in source_records:
        print(
            f'{record["source_customer_id"]} '
            f'-> {record["nik"]} '
            f'-> {record["full_name"]}'
        )


def main() -> None:
    df = load_customer_data()

    # ---------------------------------------------------------------
    # Deterministic customer selection.
    # These are intentionally explicit for the PoC.
    # ---------------------------------------------------------------

    c1 = get_customer(df, "C00001")
    c2 = get_customer(df, "C00002")
    c3 = get_customer(df, "C00003")
    c4 = get_customer(df, "C00004")

    # Safety assertions.
    assert c1["customer_id"] == "C00001"
    assert c2["customer_id"] == "C00002"
    assert c3["customer_id"] == "C00003"
    assert c4["customer_id"] == "C00004"

    # ===============================================================
    # CRM
    # ===============================================================

    crm = [
        build_source_record("CRM001", c1),
        build_source_record("CRM002", c2),
        build_source_record("CRM003", c3),
        build_source_record("CRM004", c4),
        {
            "source_customer_id": "CRM005",
            "nik": "9999999999999999",
            "full_name": "Budi Santoso",
            "phone": "081222333444",
            "email": "budi.santoso@example.com",
            "birth_date": "1990-01-01",
        },
    ]

    # CRM003 is the identity-conflict candidate.
    crm[2]["full_name"] = "Ratna Saptono"

    # ===============================================================
    # LOS
    # ===============================================================

    los = [
        build_source_record("LOS001", c1),
        build_source_record("LOS002", c2),
        build_source_record("LOS003", c3),
        build_source_record("LOS004", c4),
        {
            "source_customer_id": "LOS005",
            "nik": "8888888888888888",
            "full_name": "Andi Setiawan",
            "phone": "081999888777",
            "email": "andi.setiawan@example.com",
            "birth_date": "1991-03-15",
        },
    ]

    # Slight name variation.
    los[0]["full_name"] = "Rafid Kuswoyo"
    los[1]["full_name"] = "Dewi P."

    # Identity conflict on same NIK.
    los[2]["full_name"] = "Drs. Gina Putra"
    los[2]["email"] = "gina.putra@example.com"

    # Missing email.
    los[3]["email"] = None

    # ===============================================================
    # MOBILE
    # ===============================================================

    mobile = [
        build_source_record("MOB001", c1),
        build_source_record("MOB002", c2),
        build_source_record("MOB003", c3),
        build_source_record("MOB004", c4),
        {
            "source_customer_id": "MOB005",
            "nik": "7777777777777777",
            "full_name": "Andi Setiawan",
            "phone": "081111222333",
            "email": "andim@example.com",
            "birth_date": "1991-03-15",
        },
    ]

    # Name variation + missing email.
    mobile[0]["full_name"] = "Rafid K."
    mobile[0]["email"] = None

    # Missing NIK.
    mobile[1]["nik"] = None

    # Name variation.
    mobile[2]["full_name"] = "Ratna S."

    # ===============================================================
    # Print source-to-base mapping BEFORE writing.
    # ===============================================================

    print("\n=== BASE CUSTOMER USED ===")

    for customer in [c1, c2, c3, c4]:
        print(
            f'{customer["customer_id"]} '
            f'-> {customer["nik"]} '
            f'-> {customer["full_name"]}'
        )

    print_mapping("CRM", crm)
    print_mapping("LOS", los)
    print_mapping("MOBILE", mobile)

    # ===============================================================
    # Write files.
    # ===============================================================

    pd.DataFrame(crm).to_csv(
        OUTPUT_DIR / "crm_customer.csv",
        index=False,
    )

    pd.DataFrame(los).to_csv(
        OUTPUT_DIR / "los_customer.csv",
        index=False,
    )

    pd.DataFrame(mobile).to_csv(
        OUTPUT_DIR / "mobile_customer.csv",
        index=False,
    )

    print("\nGenerated datasets:")
    print("  CRM    : 5")
    print("  LOS    : 5")
    print("  MOBILE : 5")


if __name__ == "__main__":
    main()