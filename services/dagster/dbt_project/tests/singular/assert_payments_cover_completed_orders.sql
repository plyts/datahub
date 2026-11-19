-- Une commande "completed" doit être entièrement payée (tolérance 1 centime).
{{ config(severity='warn') }}
select order_id, order_amount, paid_amount
from {{ ref('fct_orders') }}
where status = 'completed'
  and abs(order_amount - paid_amount) > 0.01
