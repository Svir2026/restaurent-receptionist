-- Run this in the Supabase SQL Editor to create the required tables.

CREATE TABLE IF NOT EXISTS orders (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id        text UNIQUE NOT NULL,
    customer_name   text NOT NULL DEFAULT '',
    customer_phone  text NOT NULL DEFAULT '',
    order_status    text NOT NULL DEFAULT 'new order',
    created_at      text NOT NULL DEFAULT '',
    order_type      text NOT NULL DEFAULT '',
    order_items     jsonb NOT NULL DEFAULT '[]'::jsonb,
    party_size      text NOT NULL DEFAULT '',
    dine_in_time    text NOT NULL DEFAULT '',
    pickup_time     text NOT NULL DEFAULT '',
    total           double precision,
    notes           text NOT NULL DEFAULT '',
    source          text NOT NULL DEFAULT '',
    cancellation_reason text NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_orders_order_id ON orders (order_id);
CREATE INDEX IF NOT EXISTS idx_orders_customer_phone ON orders (customer_phone);
CREATE INDEX IF NOT EXISTS idx_orders_order_status ON orders (order_status);


CREATE TABLE IF NOT EXISTS call_logs (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    logged_at           text NOT NULL DEFAULT '',
    webhook_type        text NOT NULL DEFAULT '',
    conversation_id     text NOT NULL DEFAULT '',
    agent_id            text NOT NULL DEFAULT '',
    status              text NOT NULL DEFAULT '',
    duration_secs       text NOT NULL DEFAULT '',
    caller_number       text NOT NULL DEFAULT '',
    has_audio           text NOT NULL DEFAULT '',
    has_user_audio      text NOT NULL DEFAULT '',
    has_response_audio  text NOT NULL DEFAULT '',
    transcript_text     text NOT NULL DEFAULT '',
    payload_json        text NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_call_logs_conversation_id ON call_logs (conversation_id);
CREATE INDEX IF NOT EXISTS idx_call_logs_caller_number ON call_logs (caller_number);
