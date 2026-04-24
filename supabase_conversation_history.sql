-- public.conversation_history: one row per chat session (keyed by session_id).
-- Used by src.conversation_store (insert/upsert turns, optional feedback on messages JSONB).

CREATE TABLE IF NOT EXISTS public.conversation_history (
  session_id uuid NOT NULL,
  messages jsonb NOT NULL DEFAULT '[]'::jsonb,
  tool_call jsonb,
  tool_call_result jsonb,
  response_feedback jsonb NOT NULL DEFAULT '[]'::jsonb,
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT conversation_history_pkey PRIMARY KEY (session_id)
);

COMMENT ON TABLE public.conversation_history IS
  'TrailerPlace chat: messages is an array of per-turn objects with user, assistant, optional tool metadata.';

COMMENT ON COLUMN public.conversation_history.response_feedback IS
  'Append-only array of feedback entries. Per-turn detail also on messages[turn].user_feedback.';
