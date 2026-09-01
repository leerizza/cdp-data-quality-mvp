select
    customer_id,
    nik
from {{ ref('stg_customer') }}
where nik is not null
  and (
      length(nik) <> 16
      or not regexp_matches(nik, '^[0-9]{16}$')
  )