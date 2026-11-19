{#
  Returns the WHERE predicate used by incremental models.
  - When Dagster passes a partition window (vars min_date / max_date), only that window is processed.
  - Otherwise the last `incremental_lookback_days` days already present in the target are re-processed.
#}
{% macro incremental_window(date_column) -%}
    {%- set min_date = var('min_date', none) -%}
    {%- set max_date = var('max_date', none) -%}
    {%- if min_date and max_date -%}
        {{ date_column }} >= cast('{{ min_date }}' as date)
        and {{ date_column }} < cast('{{ max_date }}' as date)
    {%- else -%}
        {{ date_column }} >= (
            select coalesce(max({{ date_column }}), date '1900-01-01') - interval {{ var('incremental_lookback_days', 3) }} day
            from {{ this }}
        )
    {%- endif -%}
{%- endmacro %}
