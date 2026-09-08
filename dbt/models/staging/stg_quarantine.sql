select
    document_id,
    source_key,
    member_name,
    stage,
    reason,
    error,
    batch_id,
    quarantined_at,
    model,
    prompt_id,
    released_at,
    released_at is not null as released
from {{ source('raw', 'quarantine') }}
