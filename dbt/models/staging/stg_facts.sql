-- One row per fact. iXBRL tags the same fact wherever it is displayed (balance sheet and
-- again in a note), so raw.facts holds one row per occurrence. This model collapses them.
--
-- Every pick is deterministic: ordered ARRAY_AGG, never ANY_VALUE, so the answer key reads
-- the same on every query. Agreement is a numeric property; a nil occurrence counts as its
-- own distinct value so it cannot mask a disagreement. Non-numeric facts legitimately differ
-- in display between the hidden machine form (2025-12-31) and the printed form
-- (31 December 2025): the hidden form wins `text`, the printed form is `display_text`.
with occ as (
    select * from {{ ref('stg_fact_occurrences') }}
),

grouped as (
    select
        document_id,
        concept,
        taxonomy,
        context_id,
        array_agg(namespace order by namespace limit 1)[offset(0)] as namespace,
        array_agg(taxonomy_version ignore nulls order by taxonomy_version limit 1)[safe_offset(0)]
            as taxonomy_version,
        logical_or(is_numeric) as is_numeric,
        array_agg(value ignore nulls order by value limit 1)[safe_offset(0)] as value,
        array_agg(text order by in_hidden desc, text limit 1)[offset(0)] as text,
        array_agg(text order by in_hidden asc, text limit 1)[offset(0)] as display_text,
        array_agg(unit ignore nulls order by unit limit 1)[safe_offset(0)] as unit,
        array_agg(decimals ignore nulls order by decimals limit 1)[safe_offset(0)] as decimals,
        array_agg(scale ignore nulls order by scale limit 1)[safe_offset(0)] as scale,
        array_agg(sign ignore nulls order by sign limit 1)[safe_offset(0)] as sign,
        logical_and(in_hidden) as hidden_everywhere,
        array_agg(period_start ignore nulls order by period_start limit 1)[safe_offset(0)]
            as period_start,
        array_agg(period_end ignore nulls order by period_end limit 1)[safe_offset(0)] as period_end,
        array_agg(instant ignore nulls order by instant limit 1)[safe_offset(0)] as instant,
        logical_or(dimensional) as dimensional,
        array_agg(dimensions ignore nulls order by dimensions limit 1)[safe_offset(0)] as dimensions,
        count(*) as occurrences,
        count(distinct if(is_numeric, coalesce(cast(value as string), '<nil>'), null))
            as distinct_values,
        count(distinct text) as distinct_texts,
        array_agg(batch_id order by batch_id limit 1)[offset(0)] as batch_id
    from occ
    group by document_id, concept, taxonomy, context_id
)

select
    concat(document_id, '|', taxonomy, ':', concept, '|', context_id) as fact_key,
    is_numeric and distinct_values > 1 as inconsistent,
    *
from grouped
