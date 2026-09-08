select
    run_id,
    model,
    prompt_id,
    batch_id,
    status,
    cap,
    selected,
    extracted,
    quarantined,
    input_tokens,
    output_tokens,
    cost_usd,
    started_at,
    finished_at,
    timestamp_diff(finished_at, started_at, second) as duration_seconds,
    error
from {{ source('raw', 'extract_runs') }}
