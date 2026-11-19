-- Chiffre d'affaires quotidien.
select
    order_date,
    count(*)                                                    as order_count,
    count(*) filter (where is_revenue)                          as revenue_order_count,
    sum(case when is_revenue then order_amount else 0 end)      as revenue,
    sum(paid_amount)                                            as cash_collected,
    sum(outstanding_amount)                                     as outstanding,
    round(avg(order_amount), 2)                                 as avg_basket
from {{ ref('fct_orders') }}
group by order_date
