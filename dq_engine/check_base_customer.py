import pandas as pd

df = pd.read_csv(
    "../../data/customer.csv",
    dtype={
        "customer_id": "string",
        "nik": "string",
        "full_name": "string",
        "phone": "string",
        "email": "string",
        "birth_date": "string",
    },
)

ids = [
    "C00001",
    "C00002",
    "C00003",
    "C00004",
    "C00005",
]

print(
    df[df["customer_id"].isin(ids)][
        [
            "customer_id",
            "nik",
            "full_name",
            "phone",
            "email",
            "birth_date",
        ]
    ].to_string(index=False)
)