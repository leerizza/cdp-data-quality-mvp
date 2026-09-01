select
    'LOS' as source_system,
    source_customer_id,
    trim(nik) as nik,
    trim(full_name) as full_name,
    trim(phone) as phone,
    lower(trim(email)) as email,
    birth_date
from {{ ref('los_customer') }}