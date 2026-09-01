select
    nik
from {{ ref('stg_customer') }}
where nik is not null
group by nik
having count(*) > 1