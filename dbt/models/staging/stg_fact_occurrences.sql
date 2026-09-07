-- Every tagged occurrence, typed, with the taxonomy split out of the namespace URI.
-- Namespaces look like http://xbrl.frc.org.uk/fr/2025-01-01/core: family "fr", version
-- "2025-01-01", taxonomy "core". The same concept under different versions is still the
-- same concept for evaluation purposes, so stg_facts keys on (concept, taxonomy) only.
select
    document_id,
    concept,
    namespace,
    regexp_extract(namespace, r'/(\d{4}-\d{2}-\d{2})/') as taxonomy_version,
    -- '' when the parser could not resolve the tag's prefix; never NULL, so keys stay whole.
    coalesce(regexp_extract(namespace, r'/([^/]+)$'), 'unresolved') as taxonomy,
    context_id,
    is_numeric,
    value,
    text,
    unit,
    decimals,
    format,
    scale,
    sign,
    in_hidden,
    period_start,
    period_end,
    instant,
    dimensional,
    batch_id
from {{ source('raw', 'facts') }}
