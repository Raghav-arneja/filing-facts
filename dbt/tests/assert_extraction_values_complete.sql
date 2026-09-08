-- Every extraction must flatten to exactly 9 concepts x 2 periods. Fewer means the JSON is
-- missing a field the schema requires; more means a duplicate.
select document_id, model, prompt_id, count(*) as rows_
from {{ ref('stg_extraction_values') }}
group by 1, 2, 3
having count(*) != 18
