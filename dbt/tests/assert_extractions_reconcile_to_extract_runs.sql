-- Per succeeded batch, the ledger's extracted and quarantined counts must equal the rows
-- present. Low-confidence documents appear in both tables and are counted in both, in the
-- ledger and here.
with ledger as (
    select batch_id, sum(extracted) as extracted, sum(quarantined) as quarantined
    from {{ ref('stg_extract_runs') }}
    where status = 'succeeded'
    group by batch_id
),

present as (
    select
        batch_id,
        countif(kind = 'extraction') as extracted,
        countif(kind = 'quarantine') as quarantined
    from (
        select batch_id, 'extraction' as kind from {{ ref('stg_extractions') }}
        union all
        select batch_id, 'quarantine' from {{ ref('stg_quarantine') }} where stage = 'extract'
    )
    group by batch_id
)

select ledger.batch_id, ledger.extracted, present.extracted as rows_extracted,
       ledger.quarantined, present.quarantined as rows_quarantined
from ledger
left join present using (batch_id)
where ledger.extracted != coalesce(present.extracted, 0)
   or ledger.quarantined != coalesce(present.quarantined, 0)
