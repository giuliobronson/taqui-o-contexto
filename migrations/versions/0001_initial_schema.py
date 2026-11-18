"""Initial schema

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute("CREATE TYPE game_status AS ENUM ('scheduled', 'locked', 'open', 'finished')")
    op.execute("CREATE TYPE member_role AS ENUM ('player', 'watcher')")

    op.execute(
        """
        CREATE TABLE profiles (
            id           uuid PRIMARY KEY REFERENCES auth.users (id) ON DELETE CASCADE,
            email        text NOT NULL,
            display_name text NOT NULL,
            avatar_url   text,
            is_admin     boolean NOT NULL DEFAULT false,
            created_at   timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE vocabulary (
            model_version text NOT NULL,
            word          text NOT NULL,
            embedding     vector NOT NULL,
            PRIMARY KEY (model_version, word)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE games (
            id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            secret_word     text NOT NULL,
            model_version   text NOT NULL,
            vocabulary_size integer NOT NULL DEFAULT 0,
            status          game_status NOT NULL DEFAULT 'scheduled',
            starts_at       timestamptz NOT NULL,
            ends_at         timestamptz NOT NULL,
            unlocked_at     timestamptz,
            unlocked_by     uuid REFERENCES profiles (id) ON DELETE SET NULL,
            winner_id       uuid REFERENCES profiles (id) ON DELETE SET NULL,
            finished_at     timestamptz,
            created_by      uuid REFERENCES profiles (id) ON DELETE SET NULL,
            created_at      timestamptz NOT NULL DEFAULT now(),

            CONSTRAINT games_period_valid CHECK (ends_at > starts_at),
            CONSTRAINT games_finished_consistent CHECK (
                (status = 'finished') = (finished_at IS NOT NULL)
            ),
            CONSTRAINT games_winner_only_when_finished CHECK (
                winner_id IS NULL OR status = 'finished'
            ),
            CONSTRAINT games_unlocked_consistent CHECK (
                status NOT IN ('scheduled', 'locked') OR unlocked_at IS NULL
            ),
            -- Periods may not overlap. A game that finished early frees the rest
            -- of its period, so the effective end is finished_at when present.
            CONSTRAINT games_no_overlap EXCLUDE USING gist (
                tstzrange(starts_at, LEAST(ends_at, COALESCE(finished_at, ends_at))) WITH &&
            )
        )
        """
    )
    # At most one active (locked or open) game at any time.
    op.execute(
        "CREATE UNIQUE INDEX games_single_active ON games ((true)) "
        "WHERE status IN ('locked', 'open')"
    )

    op.execute(
        """
        CREATE TABLE game_members (
            game_id   bigint NOT NULL REFERENCES games (id) ON DELETE CASCADE,
            user_id   uuid NOT NULL REFERENCES profiles (id) ON DELETE CASCADE,
            role      member_role NOT NULL DEFAULT 'player',
            joined_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (game_id, user_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE word_rankings (
            game_id bigint NOT NULL REFERENCES games (id) ON DELETE CASCADE,
            word    text NOT NULL,
            rank    integer NOT NULL CHECK (rank >= 1),
            PRIMARY KEY (game_id, word)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE guesses (
            id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            user_id    uuid NOT NULL REFERENCES profiles (id) ON DELETE CASCADE,
            game_id    bigint NOT NULL REFERENCES games (id) ON DELETE CASCADE,
            word       text NOT NULL,
            rank       integer NOT NULL CHECK (rank >= 1),
            guess_date date NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT guesses_one_per_day UNIQUE (user_id, game_id, guess_date),
            CONSTRAINT guesses_no_repeat UNIQUE (user_id, game_id, word)
        )
        """
    )
    op.execute("CREATE INDEX guesses_game_rank ON guesses (game_id, rank)")

    op.execute(
        """
        CREATE TABLE hints (
            id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            game_id    bigint NOT NULL REFERENCES games (id) ON DELETE CASCADE,
            author_id  uuid NOT NULL REFERENCES profiles (id) ON DELETE CASCADE,
            body       varchar(280) NOT NULL CHECK (length(trim(body)) > 0),
            created_at timestamptz NOT NULL DEFAULT now(),
            deleted_at timestamptz,
            deleted_by uuid REFERENCES profiles (id) ON DELETE SET NULL
        )
        """
    )
    op.execute("CREATE INDEX hints_game_created ON hints (game_id, created_at DESC)")

    op.execute(
        """
        CREATE TABLE prizes (
            id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            game_id    bigint NOT NULL UNIQUE REFERENCES games (id) ON DELETE CASCADE,
            user_id    uuid NOT NULL REFERENCES profiles (id) ON DELETE CASCADE,
            code       text NOT NULL UNIQUE,
            claimed_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    for table in (
        "prizes",
        "hints",
        "guesses",
        "word_rankings",
        "game_members",
        "games",
        "vocabulary",
        "profiles",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("DROP TYPE IF EXISTS member_role")
    op.execute("DROP TYPE IF EXISTS game_status")
