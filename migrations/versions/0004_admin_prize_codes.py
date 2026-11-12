"""Coupons come from the admin panel

- games.prize_code: the coupon the admin registers for the game; it is copied
  to prizes when someone wins. prize_description says what it is worth.
- prizes.code is no longer unique: the admin may reuse a code in two games.

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE games
            ADD COLUMN prize_code text
                CONSTRAINT games_prize_code_not_blank CHECK (length(trim(prize_code)) > 0),
            ADD COLUMN prize_description text
        """
    )
    op.execute("ALTER TABLE prizes DROP CONSTRAINT prizes_code_key")


def downgrade() -> None:
    op.execute("ALTER TABLE prizes ADD CONSTRAINT prizes_code_key UNIQUE (code)")
    op.execute("ALTER TABLE games DROP COLUMN prize_description, DROP COLUMN prize_code")
