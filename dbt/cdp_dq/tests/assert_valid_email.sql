select
    customer_id,
    email
from {{ ref('stg_customer') }}
where email is not null
  and not regexp_matches(
      lower(email),
      '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$'
  )