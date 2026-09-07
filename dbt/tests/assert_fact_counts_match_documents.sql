-- Each document's recorded fact_count must equal its occurrence rows in raw.facts.
select d.document_id, d.fact_count, count(o.document_id) as occurrence_rows
from {{ ref('stg_documents') }} as d
left join {{ ref('stg_fact_occurrences') }} as o using (document_id)
group by d.document_id, d.fact_count
having d.fact_count != count(o.document_id)
