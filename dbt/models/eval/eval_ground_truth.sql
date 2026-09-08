-- The answer key: one XBRL-tagged value per document, concept and period, for the concepts
-- the extraction schema asks about (taken from the flattened values, so the two cannot drift).
--
-- Rules, each of which the unit test pins:
--   * Non-dimensional facts only, except creditors, where the truth is the member tagged
--     "within one year" and nothing else, because that is what the prompt asks for.
--   * A fact dated on the balance sheet date is the current period; earlier is prior. When a
--     period has facts at more than one date (rare), the latest date wins.
--   * Facts whose own occurrences disagree are excluded upstream (stg_facts.inconsistent).
--     If the chosen date still carries more than one distinct value, the cell is ambiguous
--     and scored as no ground truth.
with concepts as (
    select distinct concept from {{ ref('stg_extraction_values') }}
),

docs as (
    select document_id, period_end as doc_period_end from {{ ref('stg_documents') }}
),

facts as (
    select
        f.document_id,
        f.concept,
        f.value,
        coalesce(f.instant, f.period_end) as fact_date,
        d.doc_period_end
    from {{ ref('stg_facts') }} as f
    inner join docs as d using (document_id)
    inner join concepts using (concept)
    where f.is_numeric
      and not f.inconsistent
      and f.value is not null
      and (
          (f.concept != 'Creditors' and not f.dimensional)
          or (
              f.concept = 'Creditors'
              and f.dimensions = 'MaturitiesOrExpirationPeriodsDimension=WithinOneYear'
          )
      )
),

periodised as (
    select
        *,
        case
            when fact_date = doc_period_end then 'current'
            when fact_date < doc_period_end then 'prior'
        end as period
    from facts
    where fact_date <= doc_period_end
),

per_date as (
    select
        document_id,
        concept,
        period,
        fact_date,
        min(value) as value,
        count(distinct value) as distinct_values
    from periodised
    group by document_id, concept, period, fact_date
),

latest as (
    select
        *,
        row_number() over (partition by document_id, concept, period order by fact_date desc) as rn
    from per_date
)

select
    document_id,
    concept,
    period,
    value,
    fact_date,
    distinct_values > 1 as ambiguous
from latest
where rn = 1
