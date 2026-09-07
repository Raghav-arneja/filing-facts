{% macro create_schema(relation) -%}
    {#- Datasets are Terraform's job. dbt only reaches this when a target dataset is missing,
        which means the environment is wrong; refuse rather than create one silently. -#}
    {{ exceptions.raise_compiler_error(
        "Dataset " ~ relation.without_identifier() ~ " does not exist. Create it with Terraform "
        ~ "(infra/stage1); dbt never creates datasets in this project.") }}
{%- endmacro %}
