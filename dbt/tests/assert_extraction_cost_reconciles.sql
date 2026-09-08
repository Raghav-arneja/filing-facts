-- The ledger's cost for a succeeded batch must equal the sum of its rows' costs, within
-- rounding. A gap means rows were lost, loaded twice, or the ledger lies about spend.
with rows_ as (
    select model, prompt_id, batch_id, sum(cost_usd) as row_cost
    from {{ ref('stg_extractions') }}
    group by 1, 2, 3
),

ledger as (
    select model, prompt_id, batch_id, sum(cost_usd) as ledger_cost
    from {{ ref('stg_extract_runs') }}
    where status = 'succeeded'
    group by 1, 2, 3
)

select *
from ledger
full outer join rows_ using (model, prompt_id, batch_id)
where abs(coalesce(ledger_cost, 0) - coalesce(row_cost, 0)) > 0.001
