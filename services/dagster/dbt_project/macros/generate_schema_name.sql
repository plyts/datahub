{#
  Use the custom schema name as-is (staging, intermediate, marts, reference)
  instead of dbt's default "<target_schema>_<custom_schema>" concatenation.
  Environments are isolated by DuckDB file (one file per environment).
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
