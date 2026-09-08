select
    run_id,
    source_key,
    batch_id,
    status,
    cap,
    selected,
    parsed,
    quarantined,
    facts,
    started_at,
    finished_at,
    timestamp_diff(finished_at, started_at, second) as duration_seconds,
    error,
    parser_version,
    coalesce(reparse, false) as reparse
from {{ source('raw', 'parse_runs') }}
