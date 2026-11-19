{#
  Generic test: fails when the model has fewer than `min_rows` rows.
  Usage:
    data_tests:
      - row_count_min:
          arguments:
            min_rows: 1
#}
{% test row_count_min(model, min_rows=1) %}
    select count(*) as row_count
    from {{ model }}
    having count(*) < {{ min_rows }}
{% endtest %}
