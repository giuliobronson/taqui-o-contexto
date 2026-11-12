"""Show a word's closest vocabulary words: a quick check before choosing a secret.

Usage:
    uv run python -m pipeline.neighbors "spy family" --top 40
    uv run python -m pipeline.neighbors "spy family" --rank anime espião família
"""

import argparse
import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from pipeline.build_vocabulary import DEFAULT_MODEL_VERSION, normalize

_RANKED = """
    SELECT v.word,
           ROW_NUMBER() OVER (ORDER BY v.embedding <=> s.embedding, v.word) AS rank,
           1 - (v.embedding <=> s.embedding) AS similarity
      FROM vocabulary v
      CROSS JOIN (SELECT embedding FROM vocabulary
                   WHERE model_version = :model AND word = :word) s
     WHERE v.model_version = :model
"""


async def run(word: str, model: str, top: int, ranks: list[str]) -> None:
    engine = create_async_engine(get_settings().database_url)
    async with engine.connect() as conn:
        total = await conn.scalar(
            text("SELECT count(*) FROM vocabulary WHERE model_version = :m"), {"m": model}
        )
        rows = (
            await conn.execute(
                text(f"SELECT * FROM ({_RANKED}) r ORDER BY rank LIMIT :top"),
                {"model": model, "word": word, "top": top},
            )
        ).all()
        if not rows:
            print(f"'{word}' não está no vocabulário '{model}'.")
            return
        print(f"Vizinhos de '{word}' ({total} palavras):")
        for row in rows:
            print(f"  {row.rank:>6}  {row.similarity:.3f}  {row.word}")
        if ranks:
            print("Posições:")
            found = await conn.execute(
                text(f"SELECT * FROM ({_RANKED}) r WHERE word = ANY(:words) ORDER BY rank"),
                {"model": model, "word": word, "words": ranks},
            )
            for row in found:
                print(f"  {row.rank:>6}  {row.similarity:.3f}  {row.word}")
    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("word")
    parser.add_argument("--model-version", default=DEFAULT_MODEL_VERSION)
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--rank", nargs="*", default=[], help="palavras cuja posição mostrar")
    args = parser.parse_args()
    asyncio.run(
        run(normalize(args.word), args.model_version, args.top, [normalize(w) for w in args.rank])
    )


if __name__ == "__main__":
    main()
