-- Data Hub application schema (PostgreSQL). Applied by `python -m datahub_ui.db.migrate`.
create schema if not exists datahub;

-- 01 Pipeline Management: pipelines defined from the UI, compiled into Dagster jobs.
create table if not exists datahub.pipeline_definitions (
    id              serial primary key,
    name            text not null unique check (name ~ '^[a-z][a-z0-9_]{2,62}$'),
    description     text not null default '',
    selection_type  text not null check (selection_type in ('dagster', 'dbt')),
    selection       text not null check (length(trim(selection)) > 0),
    cron            text,
    timezone        text not null default 'Europe/Paris',
    enabled         boolean not null default true,
    tags            jsonb not null default '{}'::jsonb,
    created_by      text not null,
    created_at      timestamptz not null default now(),
    updated_by      text not null,
    updated_at      timestamptz not null default now()
);

-- 04 Data Catalog: business glossary.
create table if not exists datahub.glossary_terms (
    id              serial primary key,
    term            text not null unique,
    definition      text not null,
    owner           text not null default '',
    synonyms        text[] not null default '{}',
    related_assets  text[] not null default '{}',
    created_by      text not null,
    created_at      timestamptz not null default now(),
    updated_by      text not null,
    updated_at      timestamptz not null default now()
);

-- 10 Administration: data connections. Secrets are NEVER stored here, only a reference
-- (name of an environment variable / Docker secret) resolved by the services.
create table if not exists datahub.data_connections (
    id              serial primary key,
    name            text not null unique,
    kind            text not null check (kind in ('postgresql', 'duckdb', 'mysql', 'sqlserver', 'oracle', 's3', 'sftp', 'http', 'other')),
    host            text not null default '',
    port            integer,
    database        text not null default '',
    username        text not null default '',
    secret_ref      text not null default '' check (secret_ref ~ '^[A-Z0-9_]*$'),
    options         jsonb not null default '{}'::jsonb,
    description     text not null default '',
    created_by      text not null,
    created_at      timestamptz not null default now(),
    updated_by      text not null,
    updated_at      timestamptz not null default now()
);

-- 06 Logs & Error Management: incidents created from failed runs.
create table if not exists datahub.incidents (
    id              serial primary key,
    run_id          text not null unique,
    job_name        text not null,
    category        text not null,
    severity        text not null check (severity in ('critical', 'high', 'medium', 'low')),
    message         text not null default '',
    status          text not null default 'open' check (status in ('open', 'acknowledged', 'resolved')),
    assignee        text,
    comment         text not null default '',
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),
    resolved_at     timestamptz
);
create index if not exists incidents_status_idx on datahub.incidents (status, created_at desc);

-- 10 Administration & Security: audit trail of every user action.
create table if not exists datahub.audit_log (
    id              bigserial primary key,
    ts              timestamptz not null default now(),
    username        text not null,
    roles           text[] not null default '{}',
    action          text not null,
    target          text not null default '',
    details         jsonb not null default '{}'::jsonb,
    success         boolean not null default true
);
create index if not exists audit_log_ts_idx on datahub.audit_log (ts desc);
create index if not exists audit_log_user_idx on datahub.audit_log (username, ts desc);

-- Starter glossary.
insert into datahub.glossary_terms (term, definition, owner, synonyms, related_assets, created_by, updated_by) values
  ('Commande', 'Achat passé par un client, identifié par order_id.', 'data-engineering', '{order}', '{marts/fct_orders}', 'system', 'system'),
  ('Chiffre d''affaires', 'Somme des montants des commandes expédiées ou terminées (shipped, completed).', 'finance', '{CA,revenue}', '{marts/agg_daily_revenue,marts/fct_orders}', 'system', 'system'),
  ('Client', 'Personne ayant un compte, identifiée par customer_id.', 'analytics-engineering', '{customer}', '{marts/dim_customers}', 'system', 'system'),
  ('Lifetime value', 'Chiffre d''affaires cumulé d''un client depuis sa première commande.', 'analytics-engineering', '{LTV,CLV}', '{marts/dim_customers}', 'system', 'system'),
  ('Panier moyen', 'Montant moyen d''une commande sur la période.', 'finance', '{AOV,average basket}', '{marts/agg_daily_revenue}', 'system', 'system')
on conflict (term) do nothing;
