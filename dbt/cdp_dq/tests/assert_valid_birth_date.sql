select
    customer_id,
    birth_date
from {{ ref('stg_customer') }}
where birth_date is not null
  and birth_date > current_date