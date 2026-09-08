-- One row per extracted cell: what the model said, what the tags say, and the verdict.
--
-- Outcomes:
--   correct        value equals the ground truth exactly
--   wrong          both present, different; error_type says how
--   missed         ground truth exists, model returned null
--   unsupported    model returned a value but the filing tags no ground truth for that cell,
--                  so it cannot be verified either way (not counted as wrong)
--   tag_error      the tag is demonstrably wrong and the model is not: a count tagged with a
--                  scale of minus two (2 employees tagged as 0.02), which 344 filings do.
--                  Reported, not scored against the model.
--   not_applicable neither side has a value
--
-- Error types for `wrong`: sign_flipped, scale_1000 (off by a factor of a thousand either
-- way), period_swapped (matches the other period's truth), near_miss (within one percent),
-- other.
with cells as (
    select
        v.document_id,
        v.model,
        v.prompt_id,
        v.field,
        v.concept,
        v.period,
        v.value as extracted,
        v.confidence,
        v.evidence
    from {{ ref('stg_extraction_values') }} as v
),

truth as (
    select document_id, concept, period, value as truth
    from {{ ref('eval_ground_truth') }}
    where not ambiguous
),

other_period as (
    select
        document_id,
        concept,
        if(period = 'current', 'prior', 'current') as period,
        value as other_truth
    from {{ ref('eval_ground_truth') }}
    where not ambiguous
),

joined as (
    select
        c.*,
        t.truth,
        o.other_truth
    from cells as c
    left join truth as t using (document_id, concept, period)
    left join other_period as o using (document_id, concept, period)
),

classified as (
    select
        *,
        case
            when truth is null and extracted is null then 'not_applicable'
            when truth is null then 'unsupported'
            when extracted is null then 'missed'
            when extracted = truth then 'correct'
            when extracted != 0 and truth = extracted / 100 then 'tag_error'
            else 'wrong'
        end as outcome
    from joined
)

select
    *,
    case
        when outcome = 'tag_error' then 'truth_off_by_100'
        when outcome != 'wrong' then null
        when extracted = -truth then 'sign_flipped'
        when extracted = truth * 1000 or extracted * 1000 = truth then 'scale_1000'
        when other_truth is not null and extracted = other_truth then 'period_swapped'
        when truth != 0 and abs(extracted - truth) / abs(truth) < 0.01 then 'near_miss'
        else 'other'
    end as error_type
from classified
