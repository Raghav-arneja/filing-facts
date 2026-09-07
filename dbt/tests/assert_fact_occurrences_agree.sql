-- A numeric fact tagged in several places must carry the same value everywhere. Rows here
-- are filings whose own tags disagree. That is a property of the data, not of this build,
-- so it warns rather than fails; stg_facts.inconsistent carries it forward for Stage 4.
{{ config(severity='warn') }}

select document_id, concept, context_id, occurrences, distinct_values
from {{ ref('stg_facts') }}
where inconsistent
