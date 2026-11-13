-- TimescaleDB is optional on this host; plain Postgres is the default.
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS timescaledb;
EXCEPTION
    WHEN OTHERS THEN
        RAISE NOTICE 'timescaledb extension not available — using plain table for nav_daily';
END $$;

-- Families group variants that share the same portfolio.
CREATE TABLE IF NOT EXISTS scheme_family (
    family_id BIGSERIAL PRIMARY KEY,
  family_key TEXT NOT NULL UNIQUE,
    amc TEXT,
    base_scheme_name TEXT,
    sebi_category TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS scheme_master (
    scheme_code INTEGER PRIMARY KEY,
    family_id BIGINT REFERENCES scheme_family(family_id),
    isin_growth TEXT,
    isin_reinvest TEXT,
    amc TEXT NOT NULL DEFAULT '',
    scheme_name TEXT NOT NULL,
    plan TEXT NOT NULL DEFAULT 'unknown',
    option TEXT NOT NULL DEFAULT 'unknown',
    scheme_type TEXT NOT NULL DEFAULT 'open',
    sebi_category TEXT,
    category_raw TEXT,
    benchmark_id TEXT,
    first_nav_date DATE,
    last_nav_date DATE,
    last_nav NUMERIC(20, 8),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_scheme_master_family ON scheme_master(family_id);
CREATE INDEX IF NOT EXISTS idx_scheme_master_category ON scheme_master(sebi_category);
CREATE INDEX IF NOT EXISTS idx_scheme_master_active ON scheme_master(is_active) WHERE is_active;

CREATE TABLE IF NOT EXISTS scheme_lineage (
    id BIGSERIAL PRIMARY KEY,
    old_scheme_code INTEGER NOT NULL,
    new_scheme_code INTEGER NOT NULL,
    effective_date DATE,
    reason TEXT,
    carry_nav_history BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS nav_daily (
    scheme_code INTEGER NOT NULL REFERENCES scheme_master(scheme_code),
    nav_date DATE NOT NULL,
    nav NUMERIC(20, 8) NOT NULL,
    source TEXT NOT NULL DEFAULT 'amfi-daily',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (scheme_code, nav_date)
);

DO $$
BEGIN
    PERFORM create_hypertable('nav_daily', 'nav_date', if_not_exists => TRUE, migrate_data => TRUE);
EXCEPTION
    WHEN OTHERS THEN
        NULL;
END $$;

CREATE INDEX IF NOT EXISTS idx_nav_daily_date ON nav_daily(nav_date DESC);
CREATE INDEX IF NOT EXISTS idx_nav_daily_scheme_date ON nav_daily(scheme_code, nav_date DESC);

CREATE TABLE IF NOT EXISTS nav_anomaly (
    scheme_code INTEGER NOT NULL,
    nav_date DATE NOT NULL,
    anomaly_type TEXT NOT NULL,
    detail JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (scheme_code, nav_date, anomaly_type)
);

CREATE TABLE IF NOT EXISTS ter_daily (
    scheme_code INTEGER NOT NULL REFERENCES scheme_master(scheme_code),
    ter_date DATE NOT NULL,
    ter_pct NUMERIC(10, 4),
    plan TEXT,
    source TEXT NOT NULL DEFAULT 'amfi-ter',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (scheme_code, ter_date, plan)
);

CREATE TABLE IF NOT EXISTS aum_periodic (
    scheme_code INTEGER NOT NULL REFERENCES scheme_master(scheme_code),
    period_end DATE NOT NULL,
    aum_cr NUMERIC(18, 2),
    source TEXT NOT NULL DEFAULT 'amfi',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (scheme_code, period_end, source)
);

CREATE TABLE IF NOT EXISTS benchmark_catalog (
    benchmark_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    symbol TEXT,
    source TEXT NOT NULL DEFAULT 'nse'
);

CREATE TABLE IF NOT EXISTS benchmark_levels (
    benchmark_id TEXT NOT NULL REFERENCES benchmark_catalog(benchmark_id),
    level_date DATE NOT NULL,
    close_value NUMERIC(18, 4) NOT NULL,
    source TEXT NOT NULL DEFAULT 'nse',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (benchmark_id, level_date)
);

CREATE TABLE IF NOT EXISTS category_benchmark_map (
    sebi_category TEXT PRIMARY KEY,
    benchmark_id TEXT NOT NULL REFERENCES benchmark_catalog(benchmark_id)
);

CREATE TABLE IF NOT EXISTS macro_daily (
    macro_date DATE PRIMARY KEY,
    risk_free_annual NUMERIC(10, 6) NOT NULL,
    source TEXT NOT NULL DEFAULT 'config'
);

CREATE TABLE IF NOT EXISTS holdings_snapshot (
    family_id BIGINT NOT NULL REFERENCES scheme_family(family_id),
    as_of DATE NOT NULL,
    holding_key TEXT NOT NULL,
    asset_class TEXT,
    instrument_name TEXT,
    isin TEXT,
    weight_pct NUMERIC(10, 4),
    market_value NUMERIC(20, 2),
    sector TEXT,
    credit_rating TEXT,
    source TEXT NOT NULL DEFAULT 'enrichment',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (family_id, as_of, holding_key)
);

CREATE TABLE IF NOT EXISTS portfolio_metrics_snapshot (
    family_id BIGINT NOT NULL,
    as_of DATE NOT NULL,
    metric TEXT NOT NULL,
    value NUMERIC(18, 6),
    PRIMARY KEY (family_id, as_of, metric)
);

CREATE TABLE IF NOT EXISTS screener_wide (
    scheme_code INTEGER PRIMARY KEY REFERENCES scheme_master(scheme_code),
    as_of_date DATE NOT NULL,
    amc TEXT,
    scheme_name TEXT,
    sebi_category TEXT,
    plan TEXT,
    option TEXT,
    family_id BIGINT,
    last_nav NUMERIC(20, 8),
    day_change_pct NUMERIC(10, 4),
    aum_cr NUMERIC(18, 2),
    ter_pct NUMERIC(10, 4),
    ret_1w NUMERIC(12, 6),
    ret_1m NUMERIC(12, 6),
    ret_3m NUMERIC(12, 6),
    ret_6m NUMERIC(12, 6),
    ret_ytd NUMERIC(12, 6),
    ret_1y NUMERIC(12, 6),
    ret_3y NUMERIC(12, 6),
    ret_5y NUMERIC(12, 6),
    ret_si NUMERIC(12, 6),
    vol_1y NUMERIC(12, 6),
    mdd_1y NUMERIC(12, 6),
    mdd_3y NUMERIC(12, 6),
    beta_3y NUMERIC(12, 6),
    sharpe_3y NUMERIC(12, 6),
    alpha_3y NUMERIC(12, 6),
    rank_ret_1y_category NUMERIC(8, 4),
    rank_ret_3y_category NUMERIC(8, 4),
    rank_sharpe_3y_category NUMERIC(8, 4),
    top10_concentration_pct NUMERIC(10, 4),
    equity_pct NUMERIC(10, 4),
    has_holdings BOOLEAN NOT NULL DEFAULT FALSE,
    data_freshness JSONB,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_screener_category ON screener_wide(sebi_category);
CREATE INDEX IF NOT EXISTS idx_screener_ret_3y ON screener_wide(ret_3y DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_screener_ter ON screener_wide(ter_pct ASC NULLS LAST);

CREATE TABLE IF NOT EXISTS raw_archive (
    id BIGSERIAL PRIMARY KEY,
    object_key TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    content_type TEXT,
    byte_size BIGINT,
    checksum_sha256 TEXT,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    id BIGSERIAL PRIMARY KEY,
    job_name TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'running',
    stats JSONB,
    error TEXT
);

INSERT INTO benchmark_catalog (benchmark_id, display_name, symbol) VALUES
    ('NIFTY50', 'Nifty 50', 'NIFTY 50'),
    ('NIFTY100', 'Nifty 100', 'NIFTY 100'),
    ('NIFTY500', 'Nifty 500', 'NIFTY 500'),
    ('NIFTY_MIDCAP_150', 'Nifty Midcap 150', 'NIFTY MIDCAP 150'),
    ('NIFTY_SMALLCAP_250', 'Nifty Smallcap 250', 'NIFTY SMALLCAP 250'),
    ('NIFTY_GSEC', 'Nifty G-Sec', 'NIFTY GS 4 8YR')
ON CONFLICT (benchmark_id) DO NOTHING;

INSERT INTO category_benchmark_map (sebi_category, benchmark_id) VALUES
    ('Equity Scheme - Large Cap Fund', 'NIFTY100'),
    ('Equity Scheme - Large & Mid Cap Fund', 'NIFTY500'),
    ('Equity Scheme - Mid Cap Fund', 'NIFTY_MIDCAP_150'),
    ('Equity Scheme - Small Cap Fund', 'NIFTY_SMALLCAP_250'),
    ('Equity Scheme - Flexi Cap Fund', 'NIFTY500'),
    ('Equity Scheme - Multi Cap Fund', 'NIFTY500'),
    ('Equity Scheme - ELSS', 'NIFTY500'),
    ('Debt Scheme - Gilt Fund', 'NIFTY_GSEC'),
    ('Other Scheme - Index Fund', 'NIFTY50')
ON CONFLICT (sebi_category) DO NOTHING;
