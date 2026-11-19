-- Nettoyage et typage des commandes brutes.
select
    cast(order_id as varchar)               as order_id,
    cast(customer_id as integer)            as customer_id,
    cast(order_date as date)                as order_date,
    lower(status)                           as status,
    product_category,
    cast(amount as decimal(12, 2))          as amount,
    cast(_partition_date as date)           as partition_date,
    cast(_loaded_at as timestamp)           as loaded_at
from {{ source('raw', 'orders') }}
