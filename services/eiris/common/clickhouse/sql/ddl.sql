CREATE DATABASE IF NOT EXISTS eiris;

-- Custom functions
CREATE FUNCTION IF NOT EXISTS embed_e5 AS (text) -> text_embedding('e5', text);

CREATE TABLE IF NOT EXISTS eiris.chat_messages
(
    ts         DateTime64(3, 'UTC') DEFAULT now64(3),
    session_id String,                  -- e.g. "tg:123456789"
    chat_id    Int64,                   -- Telegram chat.id
    user_id    Int64,                   -- Telegram from_user.id (assistant can be 0)
    role       LowCardinality(String),  -- 'user'|'assistant'|'system'
    request_id String,                  -- ties one generation together
    tg_msg_id  Int64 DEFAULT 0,         -- Telegram message_id if available
    text       String
)
ENGINE = MergeTree
PARTITION BY toDate(ts)
ORDER BY (session_id, ts)
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS eiris.system_prompt_state
(
    id         UInt8 DEFAULT 1 COMMENT 'Single row id',
    prompt     String COMMENT 'System prompt text',
    embedding  Array(Float32) MATERIALIZED embed_e5(prompt) COMMENT 'Embedding for semantic search',
    updated_at DateTime64(3, 'UTC') DEFAULT now64(3) COMMENT 'Last update time (UTC)'
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY id
COMMENT 'Latest system prompt state';

CREATE TABLE IF NOT EXISTS eiris.user_memory
(
    id                 String COMMENT 'Stable row id',
    user_id            Int64 COMMENT 'Telegram user id',
    kind               LowCardinality(String) COMMENT 'dialog_summary|facts|topic|fact_stable|fact_period',
    period             LowCardinality(String) COMMENT 'day|month|year',
    period_date_start  Date DEFAULT today() COMMENT 'Start date of the period',
    text               String COMMENT 'Memory text',
    embedding          Array(Float32) MATERIALIZED embed_e5(text) COMMENT 'Embedding for semantic search',
    updated_at         DateTime64(3, 'UTC') DEFAULT now64(3) COMMENT 'Last update time (UTC)'
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (user_id, kind, period, period_date_start, id)
COMMENT 'User memory: summaries, topics, facts';

CREATE TABLE IF NOT EXISTS eiris.self_dialog_schedule
(
    id          String,
    question    String,
    role        String,
    delay_sec   Int32,
    cron        String,
    next_run_at DateTime64(3, 'UTC'),
    active      UInt8,
    session_id  String,
    created_at  DateTime64(3, 'UTC') DEFAULT now64(3),
    updated_at  DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (id);

CREATE USER IF NOT EXISTS eiris
IDENTIFIED BY '';

GRANT SELECT, INSERT, ALTER, CREATE TABLE, DROP TABLE, TRUNCATE, OPTIMIZE
ON eiris.* TO eiris;

GRANT SHOW DATABASES ON *.* TO eiris;