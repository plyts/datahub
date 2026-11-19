-- Nettoyage et typage des paiements bruts.
select
    cast(payment_id as varchar)             as payment_id,
    cast(order_id as varchar)               as order_id,
    lower(payment_method)                   as payment_method,
    cast(amount as decimal(12, 2))          as amount,
    cast(paid_at as timestamp)              as paid_at,
    cast(_partition_date as date)           as partition_date
from {{ source('raw', 'payments') }}
