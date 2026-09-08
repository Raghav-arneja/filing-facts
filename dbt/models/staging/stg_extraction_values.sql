-- The answer flattened: one row per document, model, prompt, field and period, derived from
-- the validated JSON so there is a single source of truth. `concept` matches stg_facts.concept,
-- which is how Stage 4 joins extraction to ground truth.
--
-- The field list mirrors CONCEPTS in src/filing_facts/extract/schema.py. Keep them in step;
-- assert_extraction_values_complete fails if a field is missing from the JSON.
{% set fields = [
    ('equity', 'Equity'),
    ('net_assets', 'NetAssetsLiabilities'),
    ('net_current_assets', 'NetCurrentAssetsLiabilities'),
    ('total_assets_less_current_liabilities', 'TotalAssetsLessCurrentLiabilities'),
    ('current_assets', 'CurrentAssets'),
    ('fixed_assets', 'FixedAssets'),
    ('creditors', 'Creditors'),
    ('cash', 'CashBankOnHand'),
    ('average_employees', 'AverageNumberEmployeesDuringPeriod'),
] %}

{% set combos = [] %}
{% for field, concept in fields %}
{% for period in ['current', 'prior'] %}
{% do combos.append((field, concept, period)) %}
{% endfor %}
{% endfor %}

with base as (
    select document_id, model, prompt_id, batch_id, extraction_json
    from {{ source('raw', 'extractions') }}
)

{% for field, concept, period in combos %}
select
    document_id,
    model,
    prompt_id,
    '{{ field }}' as field,
    '{{ concept }}' as concept,
    '{{ period }}' as period,
    safe_cast(json_value(extraction_json, '$.{{ field }}.{{ period }}.value') as numeric) as value,
    json_value(extraction_json, '$.{{ field }}.{{ period }}.value') is not null as stated,
    safe_cast(json_value(extraction_json, '$.{{ field }}.{{ period }}.confidence') as float64)
        as confidence,
    json_value(extraction_json, '$.{{ field }}.{{ period }}.evidence') as evidence,
    batch_id
from base
{% if not loop.last %}union all{% endif %}
{% endfor %}
