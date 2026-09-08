-- For each source, the documents table must hold exactly the number of filings the
-- succeeded parse runs say they parsed, and vice versa. A reparse purges the source's
-- output and starts again, so only runs since the last reparse of a source count; earlier
-- runs stay in the ledger as history but no longer describe the tables.
with runs_all as (
    select * from {{ ref('stg_parse_runs') }} where status = 'succeeded'
),

last_reparse as (
    select source_key, max(started_at) as since
    from runs_all
    where reparse
    group by source_key
),

runs as (
    select r.source_key, sum(r.parsed) as parsed, sum(r.quarantined) as quarantined
    from runs_all as r
    left join last_reparse as l using (source_key)
    where l.since is null or r.started_at >= l.since
    group by r.source_key
),

docs as (
    select source_key, count(*) as documents from {{ ref('stg_documents') }} group by source_key
),

quar as (
    select source_key, count(*) as quarantined from {{ ref('stg_quarantine') }}
    where stage = 'parse'
    group by source_key
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
