-- Agrégation des paiements par commande.
with payments as (
    select * from {{ ref('stg_payments') }}
)

select
    order_id,
    count(*)                                                        as payment_count,
    sum(amount)                                                     as paid_amount,
    sum(case when payment_method = 'card' then amount else 0 end)   as card_amount,
    sum(case when payment_method = 'voucher' then amount else 0 end) as voucher_amount,
    max(paid_at)                                                    as last_paid_at
from payments
group by order_id
