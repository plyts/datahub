-- Dimension clients : profil + indicateurs de cycle de vie.
with customers as (
    select * from {{ ref('stg_customers') }}
),

countries as (
    select * from {{ ref('country_codes') }}
),

orders as (
    select * from {{ ref('fct_orders') }}
),

customer_orders as (
    select
        customer_id,
        min(order_date)                                         as first_order_date,
        max(order_date)                                         as last_order_date,
        count(*)                                                as order_count,
        sum(case when is_revenue then order_amount else 0 end)  as lifetime_value
    from orders
    group by customer_id
)

select
    customers.customer_id,
    customers.first_name,
    customers.last_name,
    customers.email,
    customers.country_code,
    countries.country_name,
    countries.region,
    customers.created_at,
    customer_orders.first_order_date,
    customer_orders.last_order_date,
    coalesce(customer_orders.order_count, 0)        as order_count,
    coalesce(customer_orders.lifetime_value, 0)     as lifetime_value,
    case
        when coalesce(customer_orders.lifetime_value, 0) >= 1000 then 'gold'
        when coalesce(customer_orders.lifetime_value, 0) >= 300 then 'silver'
        else 'bronze'
    end                                             as customer_tier
from customers
left join countries using (country_code)
left join customer_orders using (customer_id)
