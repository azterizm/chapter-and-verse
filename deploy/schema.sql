-- Chapter & Verse: Postgres schema for Heroku Postgres Essential-0 (Postgres 16).
-- Idempotent: every statement can run again on an existing database.
--
--   heroku pg:psql -a APP < deploy/schema.sql
--
-- Essential-0 limits: 1 GB storage, 20 connections, no pg_cron. Retention is therefore run by
-- the engine (a purge on startup and every hour, statements at the end of this file), not by
-- the database. Shared by every origin (Oracle now, the Hugging Face standby in Slice 4).
--
-- Privacy rules carried by the schema:
--   - the raw query text is never stored; receipts hold query_sha256 only (CHECK below);
--   - no IP address, user agent or other client identifier is stored anywhere;
--   - session state (memory tiers) is deleted after expiry.

BEGIN;

CREATE TABLE IF NOT EXISTS schema_version (
    version     integer     PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- provider quota ledger
-- One row per provider per window. The engine adds to the counters with an upsert:
--   INSERT INTO provider_quota (provider, window_kind, window_start, requests, input_tokens, ...)
--   VALUES (...) ON CONFLICT (provider, window_kind, window_start)
--   DO UPDATE SET requests = provider_quota.requests + EXCLUDED.requests, ..., updated_at = now();
-- window_start is the window's start in UTC, truncated to the window kind
-- (date_trunc('minute' | 'day' | 'month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC').
-- Groq: requests per day (1000) and tokens per minute (8000). Cloudflare: neurons per day
-- (9500 of 10000). Modal: GPU seconds per month against the $30 budget.
-- Retention: minute windows 2 days, day windows 90 days, month windows 13 months.
CREATE TABLE IF NOT EXISTS provider_quota (
    provider       text        NOT NULL CHECK (provider ~ '^[a-z0-9][a-z0-9_-]{0,31}$'),
    window_kind    text        NOT NULL CHECK (window_kind IN ('minute', 'day', 'month')),
    window_start   timestamptz NOT NULL,
    requests       integer     NOT NULL DEFAULT 0 CHECK (requests >= 0),
    input_tokens   bigint      NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
    output_tokens  bigint      NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
    neurons        numeric(14, 3) NOT NULL DEFAULT 0 CHECK (neurons >= 0),
    gpu_seconds    numeric(12, 1) NOT NULL DEFAULT 0 CHECK (gpu_seconds >= 0),
    rate_limited   integer     NOT NULL DEFAULT 0 CHECK (rate_limited >= 0),
    errors         integer     NOT NULL DEFAULT 0 CHECK (errors >= 0),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (provider, window_kind, window_start)
);
COMMENT ON TABLE provider_quota IS
    'Provider usage per window; the rotation reads it before each call. No user data.';
COMMENT ON COLUMN provider_quota.rate_limited IS 'Count of HTTP 429 responses in the window.';

CREATE INDEX IF NOT EXISTS provider_quota_updated_at ON provider_quota (updated_at);

-- ---------------------------------------------------------------- receipts
-- One Answer Fact Object per answer (docs/ARCHITECTURE.md 4.8). answer_id is
-- sha256_hex(canonical_json(fact)), so the same answer is stored once. fact holds
-- query_sha256, never the query.
-- Retention: 180 days by default (a receipt is about 3 to 6 KB; 1 GB holds well over 100,000).
CREATE TABLE IF NOT EXISTS receipts (
    answer_id   text        PRIMARY KEY CHECK (answer_id ~ '^[0-9a-f]{64}$'),
    fact        jsonb       NOT NULL CHECK (
                    jsonb_typeof(fact) = 'object'
                    AND NOT (fact ? 'query')
                    AND NOT (fact ? 'query_text')
                ),
    created_at  timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE receipts IS
    'Answer Fact Objects keyed by their own hash. Never holds the raw query.';

CREATE INDEX IF NOT EXISTS receipts_created_at ON receipts (created_at);

-- ---------------------------------------------------------------- feedback
-- A verdict on an answer, with an optional short note. The note is free text typed by a user,
-- so it is capped and kept for a shorter time than the verdict.
-- Retention: note cleared after 90 days; row deleted with its receipt (cascade) or at 180 days.
CREATE TABLE IF NOT EXISTS feedback (
    feedback_id  bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    answer_id    text        NOT NULL REFERENCES receipts (answer_id) ON DELETE CASCADE,
    verdict      text        NOT NULL CHECK (verdict IN ('up', 'down')),
    note         text        CHECK (note IS NULL OR char_length(note) <= 1000),
    created_at   timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE feedback IS 'User verdicts on answers. No client identifiers.';

CREATE INDEX IF NOT EXISTS feedback_answer_id ON feedback (answer_id);
CREATE INDEX IF NOT EXISTS feedback_created_at ON feedback (created_at);

-- ---------------------------------------------------------------- sessions
-- session_id is the id the Worker mints after Turnstile passes (22 base64url characters) and
-- forwards as X-CV-Session-Id. state holds the memory tiers 1 to 3 (plan section 5) and may
-- quote the user's questions, so the whole row is deleted once expires_at passes.
-- Retention: deleted at expiry (the Worker's token lives 2 hours; expires_at may extend it).
CREATE TABLE IF NOT EXISTS sessions (
    session_id  text        PRIMARY KEY CHECK (session_id ~ '^[A-Za-z0-9_-]{16,64}$'),
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL,
    state       jsonb       NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(state) = 'object'),
    CHECK (expires_at > created_at),
    CHECK (expires_at <= created_at + interval '7 days')
);
COMMENT ON TABLE sessions IS
    'Short-lived conversation state (memory tiers). Deleted after expires_at.';

CREATE INDEX IF NOT EXISTS sessions_expires_at ON sessions (expires_at);

INSERT INTO schema_version (version) VALUES (1) ON CONFLICT (version) DO NOTHING;

COMMIT;

-- ---------------------------------------------------------------- retention (run by the engine)
-- These are the purge statements the engine runs hourly. They are commented out here so that
-- applying the schema never deletes data by itself.
--
-- DELETE FROM sessions WHERE expires_at < now();
-- UPDATE feedback SET note = NULL WHERE note IS NOT NULL AND created_at < now() - interval '90 days';
-- DELETE FROM feedback WHERE created_at < now() - interval '180 days';
-- DELETE FROM receipts WHERE created_at < now() - interval '180 days';
-- DELETE FROM provider_quota
--  WHERE (window_kind = 'minute' AND window_start < now() - interval '2 days')
--     OR (window_kind = 'day'    AND window_start < now() - interval '90 days')
--     OR (window_kind = 'month'  AND window_start < now() - interval '13 months');
