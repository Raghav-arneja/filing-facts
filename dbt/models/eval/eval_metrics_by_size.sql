-- Recall by filing size: long accounts are where extraction is expected to struggle.
with sized as (
    select
        document_id,
        case
            when text_chars < 2000 then '1: under 2k chars'
            when text_chars < 5000 then '2: 2k to 5k'
            when text_chars < 15000 then '3: 5k to 15k'
            else '4: over 15k'
        end as size_band
    from {{ ref('stg_documents') }}
)

select
    s.model,
    s.prompt_id,
    z.size_band,
    count(distinct s.document_id) as documents,
    countif(s.outcome in ('correct', 'wrong', 'missed')) as verifiable,
    countif(s.outcome = 'correct') as correct,
    safe_divide(countif(s.outcome = 'correct'), countif(s.outcome in ('correct', 'wrong', 'missed')))
        as recall
from {{ ref('eval_scores') }} as s
inner join sized as z using (document_id)
group by s.model, s.prompt_id, z.size_band
