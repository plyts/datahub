-- Nettoyage et typage des clients bruts.
select
    cast(customer_id as integer)            as customer_id,
    trim(first_name)                        as first_name,
    trim(last_name)                         as last_name,
    lower(trim(email))                      as email,
    upper(country_code)                     as country_code,
    cast(created_at as timestamp)           as created_at,
    cast(_loaded_at as timestamp)           as loaded_at
from {{ source('raw', 'customers') }}
