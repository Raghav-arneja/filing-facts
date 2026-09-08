-- The headline numbers, per model, prompt and concept, plus an ALL row per model and prompt.
--   verifiable = cells where the tags give a ground truth (correct + wrong + missed)
--   precision  = correct / (correct + wrong): of the values it gave that could be checked,
--                how many were right
--   recall     = correct / verifiable: of the facts that were there to find, how many it got
--   unsupported is reported, not scored: the model answered where the tags are silent.
--   tag_error is reported, not scored: the tag is wrong and the model is not.
with scored as (
    select * from {{ ref('eval_scores') }}
),

by_concept as (
    select
        model,
        prompt_id,
        concept,
        countif(outcome in ('correct', 'wrong', 'missed')) as verifiable,
        countif(outcome = 'correct') as correct,
        countif(outcome = 'wrong') as wrong,
        countif(outcome = 'missed') as missed,
        countif(outcome = 'unsupported') as unsupported,
        countif(outcome = 'tag_error') as tag_error,
        countif(error_type = 'sign_flipped') as sign_flipped,
        countif(error_type = 'scale_1000') as scale_1000,
        countif(error_type = 'period_swapped') as period_swapped,
        countif(error_type = 'near_miss') as near_miss,
        countif(error_type = 'other') as other_error
    from scored
    group by model, prompt_id, concept
),

with_all as (
    select * from by_concept
    union all
    select
        model,
        prompt_id,
        'ALL' as concept,
        sum(verifiable),
        sum(correct),
        sum(wrong),
        sum(missed),
        sum(unsupported),
        sum(tag_error),
        sum(sign_flipped),
        sum(scale_1000),
        sum(period_swapped),
        sum(near_miss),
        sum(other_error)
    from by_concept
    group by model, prompt_id
),

runs as (
    select
        model,
        prompt_id,
        count(*) as documents,
        avg(latency_ms) as mean_latency_ms,
        sum(cost_usd) * 1000 / count(*) as usd_per_1000
    from {{ ref('stg_extractions') }}
    group by model, prompt_id
)

select
    w.*,
    safe_divide(w.correct, w.correct + w.wrong) as precision,
    safe_divide(w.correct, w.verifiable) as recall,
    r.documents,
    r.mean_latency_ms,
    r.usd_per_1000
from with_all as w
inner join runs as r using (model, prompt_id)
