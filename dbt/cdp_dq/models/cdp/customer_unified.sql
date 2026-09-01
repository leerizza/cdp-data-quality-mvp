select
    source_system,
    source_customer_id,
    nik,
    full_name,
    phone,
    email,
    birth_date
from {{ ref('stg_crm_customer') }}

union all

select
    source_system,
    source_customer_id,
    nik,
    full_name,
    phone,
    email,
    birth_date
from {{ ref('stg_los_customer') }}

union all

select
    source_system,
    source_customer_id,
    nik,
    full_name,
    phone,
    email,
    birth_date
from {{ ref('stg_mobile_customer') }}