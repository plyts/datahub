-- Le CA quotidien ne peut pas dépasser la somme des montants de commandes du jour.
select a.order_date
from {{ ref('agg_daily_revenue') }} a
join (
    select order_date, sum(order_amount) as total
    from {{ ref('fct_orders') }}
    group by order_date
) f using (order_date)
where a.revenue > f.total
