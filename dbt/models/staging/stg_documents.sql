-- One row per parsed filing. The parse job guarantees uniqueness of document_id; the
-- unique test on this model is what tells us if that guarantee ever breaks.
select
    document_id,
    source_key,
    member_name,
    company_number,
    period_end,
    byte_count,
    sha256,
    ix_namespace,
    entity_identifier,
    fact_count,
    length(text) as text_chars,
    text,
    batch_id,
    parsed_at
from {{ source('raw', 'documents') }}
