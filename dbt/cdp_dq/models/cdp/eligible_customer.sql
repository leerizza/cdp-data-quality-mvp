with latest_run as (

    select run_id

    from dq.dq_run

    where dataset = 'stg_customer'

    order by started_at desc

    limit 1
)

select
    c.customer_id,
    c.nik,
    c.full_name,
    c.phone,
    c.email,
    c.birth_date

from {{ ref('stg_customer') }} c

cross join latest_run r

where not exists (

    select 1

    from dq.quarantine_customer q

    where q.customer_id = c.customer_id

      and q.run_id = r.run_id
)