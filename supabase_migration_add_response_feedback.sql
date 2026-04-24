-- Legacy: add response_feedback to an older table. New installs use supabase_conversation_history.sql
-- (column included there). migrate_add_response_feedback.py splits on semicolons only: no semicolons
-- inside string literals in this file.

ALTER TABLE public.conversation_history
ADD COLUMN IF NOT EXISTS response_feedback jsonb NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN public.conversation_history.response_feedback IS
  'Append-only array of feedback objects. Per-turn text also in messages[turn].user_feedback.';
