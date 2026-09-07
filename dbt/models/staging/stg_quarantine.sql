select
    document_id,
    source_key,
    member_name,
    stage,
    reason,
    error,
    batch_id,
    quarantined_at
from {{ source('raw', 'quarantine') }}
