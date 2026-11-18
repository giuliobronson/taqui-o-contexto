"""Block Supabase's public REST API from every app table

Supabase exposes tables in the public schema through PostgREST using the
anon/authenticated roles. Only the backend (direct Postgres connection as the
table owner) may read or write this data, otherwise anyone could read
games.secret_word or word_rankings with the public key.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TABLES = (
    "profiles",
    "vocabulary",
    "games",
    "game_members",
    "word_rankings",
    "guesses",
    "hints",
    "prizes",
)


def upgrade() -> None:
    for table in TABLES:
        # RLS on with no policies: anon/authenticated see nothing.
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON TABLE {table} FROM anon, authenticated")
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated")


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
