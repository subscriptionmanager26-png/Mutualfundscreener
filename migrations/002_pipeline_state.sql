CREATE TABLE IF NOT EXISTS pipeline_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- AMFI publishes TER per fund (not per AMFI scheme code), with regular and direct plans side by side.
CREATE TABLE IF NOT EXISTS ter_fund_daily (
    fund_name_norm TEXT NOT NULL,
    ter_date DATE NOT NULL,
    fund_name TEXT NOT NULL,
    sebi_category TEXT,
    ter_regular_pct NUMERIC(10, 4),
    ter_direct_pct NUMERIC(10, 4),
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (fund_name_norm, ter_date)
);

ALTER TABLE screener_wide ADD COLUMN IF NOT EXISTS benchmark_id TEXT;
ALTER TABLE screener_wide ADD COLUMN IF NOT EXISTS vol_3y NUMERIC(12, 6);
ALTER TABLE screener_wide ADD COLUMN IF NOT EXISTS sharpe_1y NUMERIC(12, 6);
ALTER TABLE screener_wide ADD COLUMN IF NOT EXISTS sortino_3y NUMERIC(12, 6);
ALTER TABLE screener_wide ADD COLUMN IF NOT EXISTS mdd_5y NUMERIC(12, 6);
ALTER TABLE screener_wide ADD COLUMN IF NOT EXISTS ret_2y NUMERIC(12, 6);
ALTER TABLE screener_wide ADD COLUMN IF NOT EXISTS ret_10y NUMERIC(12, 6);
ALTER TABLE screener_wide ADD COLUMN IF NOT EXISTS nav_days INTEGER;
ALTER TABLE screener_wide ADD COLUMN IF NOT EXISTS first_nav_date DATE;
