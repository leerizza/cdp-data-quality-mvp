from pathlib import Path

import csv


PROJECT_ROOT = Path(__file__).resolve().parents[2]

SOURCE_FILE = PROJECT_ROOT / "data" / "customer.csv"
OUTPUT_DIR = PROJECT_ROOT / "data"


def load_customers():
    customers = {}

    with SOURCE_FILE.open(
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as file:
        reader = csv.DictReader(file)

        required = {
            "customer_id",
            "nik",
            "full_name",
            "phone",
            "email",
            "birth_date",
        }

        if not required.issubset(reader.fieldnames or []):
            raise ValueError(
                f"Missing columns. Found:    {reader.fieldnames}"
            )

        for row in reader:
            customer_id = row["customer_id"].strip()

            customers[customer_id] = {
                "customer_id": customer_id,
                "nik": clean(row["nik"]),
                "full_name": clean(row["full_name"]),
                "phone": clean(row["phone"]),
                "email": clean(row["email"]),
                "birth_date": clean(row["birth_date"]),
            }

    return customers


def clean(value):
    if value is None:
        return None

    value = value.strip()

    if value == "":
        return None

    if value.endswith(".0") and value[:-2].isdigit():
        value = value[:-2]

    return value


def from_customer(customers, customer_id, source_customer_id):
    if customer_id not in customers:
        raise KeyError(
            f"{customer_id} not found in customer.csv"
        )

    customer = customers[customer_id]

    return {
        "source_customer_id": source_customer_id,
        "nik": customer["nik"],
        "full_name": customer["full_name"],
        "phone": customer["phone"],
        "email": customer["email"],
        "birth_date": customer["birth_date"],
    }


def main():
    customers = load_customers()

    selected_ids = [
        "C00001",
        "C00002",
        "C00003",
        "C00004",
    ]

    print("\n=== BASE DATA ===")

    for customer_id in selected_ids:
        customer = customers[customer_id]

        print(
            f'{customer_id} | '
            f'{customer["nik"]} | '
            f'{customer["full_name"]}'
        )

    crm = [
        from_customer(
            customers,
            "C00001",
            "CRM001",
        ),
        from_customer(
            customers,
            "C00002",
            "CRM002",
        ),
        from_customer(
            customers,
            "C00003",
            "CRM003",
        ),
        from_customer(
            customers,
            "C00004",
            "CRM004",
        ),
        {
            "source_customer_id": "CRM005",
            "nik": "9999999999999999",
            "full_name": "Budi Santoso",
            "phone": "081222333444",
            "email": "budi.santoso@example.com",
            "birth_date": "1990-01-01",
        },
    ]

    # Intentional conflict:
    # CRM003 keeps C00003's identifiers but has a different name.
    crm[2]["full_name"] = "Ratna Saptono"

    los = [
        from_customer(
            customers,
            "C00001",
            "LOS001",
        ),
        from_customer(
            customers,
            "C00002",
            "LOS002",
        ),
        from_customer(
            customers,
            "C00003",
            "LOS003",
        ),
        from_customer(
            customers,
            "C00004",
            "LOS004",
        ),
        {
            "source_customer_id": "LOS005",
            "nik": "8888888888888888",
            "full_name": "Andi Setiawan",
            "phone": "081999888777",
            "email": "andi.setiawan@example.com",
            "birth_date": "1991-03-15",
        },
    ]

    los[0]["full_name"] = "Rafid Kuswoyo"
    los[1]["full_name"] = "Dewi P."
    los[2]["full_name"] = "Drs. Gina Putra"
    los[2]["email"] = "gina.putra@example.com"
    los[3]["email"] = None

    mobile = [
        from_customer(
            customers,
            "C00001",
            "MOB001",
        ),
        from_customer(
            customers,
            "C00002",
            "MOB002",
        ),
        from_customer(
            customers,
            "C00003",
            "MOB003",
        ),
        from_customer(
            customers,
            "C00004",
            "MOB004",
        ),
        {
            "source_customer_id": "MOB005",
            "nik": "7777777777777777",
            "full_name": "Andi Setiawan",
            "phone": "081111222333",
            "email": "andim@example.com",
            "birth_date": "1991-03-15",
        },
    ]

    mobile[0]["full_name"] = "Rafid K."
    mobile[0]["email"] = None

    mobile[1]["nik"] = None

    mobile[2]["full_name"] = "Ratna S."

    print("\n=== CRM ===")

    for row in crm:
        print(
            f'{row["source_customer_id"]} | '
            f'{row["nik"]} | '
            f'{row["full_name"]}'
        )

    print("\n=== LOS ===")

    for row in los:
        print(
            f'{row["source_customer_id"]} | '
            f'{row["nik"]} | '
            f'{row["full_name"]}'
        )

    print("\n=== MOBILE ===")

    for row in mobile:
        print(
            f'{row["source_customer_id"]} | '
            f'{row["nik"]} | '
            f'{row["full_name"]}'
        )

    fieldnames = [
        "source_customer_id",
        "nik",
        "full_name",
        "phone",
        "email",
        "birth_date",
    ]

    for filename, rows in [
        ("crm_customer.csv", crm),
        ("los_customer.csv", los),
        ("mobile_customer.csv", mobile),
    ]:
        output = OUTPUT_DIR / filename

        with output.open(
            "w",
            encoding="utf-8",
            newline=""
        ) as file:
            writer = csv.DictWriter(
                file,
                fieldnames=fieldnames,
            )

            writer.writeheader()
            writer.writerows(rows)

        print(f"\nWrote: {output}")


if __name__ == "__main__":
    main()