-- For each source, the documents table must hold exactly the number of filings the
-- succeeded parse runs say they parsed, and vice versa. A gap either way means rows were
-- lost, loaded twice, or loaded without a ledger row.
with runs as (
    select source_key, sum(parsed) as parsed, sum(quarantined) as quarantined
    from {{ ref('stg_parse_runs') }}
    where status = 'succeeded'
    group by source_key
),

docs as (
    select source_key, count(*) as documents from {{ ref('stg_documents') }} group by source_key
),

quar as (
    select source_key, count(*) as quarantined from {{ ref('stg_quarantine') }} group by source_key
)

select
    coalesce(runs.source_key, docs.source_key, quar.source_key) as source_key,
    coalesce(runs.parsed, 0) as parsed_per_runs,
    coalesce(docs.documents, 0) as documents,
    coalesce(runs.quarantined, 0) as quarantined_per_runs,
    coalesce(quar.quarantined, 0) as quarantined_rows
from runs
full outer join docs using (source_key)
full outer join quar using (source_key)
where coalesce(runs.parsed, 0) != coalesce(docs.documents, 0)
   or coalesce(runs.quarantined, 0) != coalesce(quar.quarantined, 0)
