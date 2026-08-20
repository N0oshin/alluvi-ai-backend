"""lock down public schema from postgrest roles

Supabase publishes every table in `public` over HTTP via PostgREST, and its
bootstrap grants CRUD on those tables to `anon` and `authenticated`. The
`anon` key is public by design (it ships inside the Flutter client), so
without this revision anyone holding it can read `users`, `refresh_tokens`
and `otp_codes` straight off the internet.

This app does not use PostgREST at all -- authorization lives in FastAPI --
so the correct posture is to close the schema entirely rather than write
row-level policies. The backend connects as `postgres`, which owns these
tables and therefore bypasses RLS, so nothing here affects it.

No-ops on non-Postgres backends and on a plain Postgres where the Supabase
roles do not exist, so local docker and the SQLite test runs still work.

Revision ID: 614f47603235
Revises: 84a2d4cd5b34
"""

from __future__ import annotations

from alembic import op

revision = "614f47603235"
down_revision = "84a2d4cd5b34"
branch_labels = None
depends_on = None

# Applied to every table in `public`, current and future, rather than a
# hardcoded list -- a new table must not be able to leak by omission.
ENABLE_RLS = """
DO $$
DECLARE t text;
BEGIN
    FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public'
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    END LOOP;
END $$;
"""

DISABLE_RLS = ENABLE_RLS.replace("ENABLE ROW LEVEL", "DISABLE ROW LEVEL")

REVOKE = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated;
        REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;
        REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM anon, authenticated;
        -- Stops tables created by later migrations from being auto-granted.
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            REVOKE ALL ON TABLES FROM anon, authenticated;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            REVOKE ALL ON SEQUENCES FROM anon, authenticated;
        -- The blanket lever: PostgREST cannot enter a schema it lacks USAGE on.
        REVOKE USAGE ON SCHEMA public FROM anon, authenticated;
    END IF;
END $$;
"""

RESTORE = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        GRANT USAGE ON SCHEMA public TO anon, authenticated;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT ALL ON TABLES TO anon, authenticated;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT ALL ON SEQUENCES TO anon, authenticated;
        GRANT ALL ON ALL TABLES IN SCHEMA public TO anon, authenticated;
        GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO anon, authenticated;
    END IF;
END $$;
"""


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(REVOKE)
    op.execute(ENABLE_RLS)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(DISABLE_RLS)
    op.execute(RESTORE)
