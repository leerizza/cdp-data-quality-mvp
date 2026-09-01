select
    customer_id,
    nik,
    full_name,
    phone,
    email,
    birth_date,

    'CUSTOMER' as entity_type,

    'customer.csv' as source_system,

    current_timestamp as record_updated_at

from {{ ref('eligible_customer') }}