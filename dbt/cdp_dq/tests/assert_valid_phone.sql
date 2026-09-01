select
    customer_id,
    phone
from {{ ref('stg_customer') }}
where phone is not null
  and not regexp_matches(
      phone,
      '^08[0-9]{8,13}$'
  )