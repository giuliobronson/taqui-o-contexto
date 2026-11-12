"""Half-precision embeddings and the word_forms table

- vocabulary.embedding becomes halfvec: 1024-dimension OpenAI vectors take half
  the space (about 100 MB for 50k words), which fits Supabase's free plan.
- word_forms maps what a player may type to a vocabulary word: inflections
  ("casas" → "casa") and spelling variants ("spy x family" → "spy family").

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE vocabulary ALTER COLUMN embedding TYPE halfvec USING embedding::halfvec"
    )

    op.execute(
        """
        CREATE TABLE word_forms (
            model_version text NOT NULL,
            form          text NOT NULL,
            word          text NOT NULL,
            PRIMARY KEY (model_version, form),
            FOREIGN KEY (model_version, word)
                REFERENCES vocabulary (model_version, word) ON DELETE CASCADE,
            CHECK (form <> word)
        )
        """
    )
    op.execute("ALTER TABLE word_forms ENABLE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON TABLE word_forms FROM anon, authenticated")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS word_forms")
    op.execute("ALTER TABLE vocabulary ALTER COLUMN embedding TYPE vector USING embedding::vector")
