-- One model answer per document, model and prompt, with the non-numeric header facts pulled
-- out of the JSON. The JSON itself stays available for anything the columns do not cover.
select
    document_id,
    source_key,
    model,
    prompt_id,
    prompt_version,
    status,
    overall_confidence,
    json_value(extraction_json, '$.company_name.value') as company_name,
    json_value(extraction_json, '$.company_number.value') as company_number,
    safe_cast(json_value(extraction_json, '$.period_start.value') as date) as period_start,
    safe_cast(json_value(extraction_json, '$.period_end.value') as date) as period_end,
    extraction_json,
    input_tokens,
    output_tokens,
    thinking_tokens,
    cost_usd,
    latency_ms,
    finish_reason,
    batch_id,
    extracted_at
from {{ source('raw', 'extractions') }}
