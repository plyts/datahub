{{
    config(
        materialized='incremental',
        unique_key='order_id',
        incremental_strategy='delete+insert',
        on_schema_change='append_new_columns',
        tags=['incremental', 'partitioned']
    )
}}

-- Table de faits des commandes, enrichie des paiements.
-- Incrémentale : Dagster passe la fenêtre de partition (vars min_date / max_date)
-- ce qui rend les backfills idempotents.
with orders as (
    select * from {{ ref('stg_orders') }}
    {% if is_incremental() %}
    where {{ incremental_window('order_date') }}
    {% endif %}
),

payments as (
    select * from {{ ref('int_order_payments') }}
)

select
    orders.order_id,
    orders.customer_id,
    orders.order_date,
    orders.status,
    orders.product_category,
    orders.amount                                       as order_amount,
    coalesce(payments.paid_amount, 0)                   as paid_amount,
    coalesce(payments.payment_count, 0)                 as payment_count,
    orders.amount - coalesce(payments.paid_amount, 0)   as outstanding_amount,
    orders.status in ('completed', 'shipped')           as is_revenue,
    orders.partition_date,
    current_timestamp                                   as dbt_updated_at
from orders
left join payments using (order_id)
