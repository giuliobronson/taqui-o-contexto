"""Build the game vocabulary: word list → filters → OpenAI embeddings → Postgres.

Usage:
    uv run --group pipeline python -m pipeline.build_vocabulary --dry-run
    uv run --group pipeline python -m pipeline.build_vocabulary

Steps (PRD RF-8, tasks T-10/T-11):
1. Candidates: the most frequent Portuguese words (wordfreq, "large" list).
2. Filters: letters only, no stopwords, no offensive words, the word must be
   known Portuguese (simplemma's dictionary) and must not be only a proper noun
   (capitalised entries of LibreOffice's pt-BR dictionary). That drops English
   words, names and typos.
   Inflections become word_forms ("casas" → "casa") when the lemma is not much
   rarer than the word itself, which rejects bad lemmas such as "filha" →
   "filhar" (see LEMMA_MAX_FREQUENCY_DROP). lemma_overrides.txt fixes the rest.
3. Extra words (pipeline/data/extra_words.txt) are always included, with their
   variants, bypassing the filters (e.g. the planned secret "spy family").
4. Embeddings from OpenAI text-embedding-3-large (1024 dims), cached on disk so
   reruns only pay for new words.
5. Replaces the vocabulary and word_forms rows of the model version.
"""

import argparse
import asyncio
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import numpy as np
import simplemma
from sqlalchemy import delete, insert
from sqlalchemy.ext.asyncio import create_async_engine
from wordfreq import top_n_list, zipf_frequency

from app.core.config import get_settings
from app.games.models import Vocabulary, WordForm

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
CACHE = ROOT / ".cache"
DICTIONARY_URL = "https://raw.githubusercontent.com/LibreOffice/dictionaries/master/pt_BR/pt_BR.dic"

OPENAI_MODEL = "text-embedding-3-large"
DIMENSIONS = 1024
DEFAULT_MODEL_VERSION = f"openai-te3l-{DIMENSIONS}-v1"

# A lemma is accepted only if it is at most this much rarer (Zipf scale) than the
# word. Bad lemmas are far rarer: "filha" 5.3 → "filhar" 1.2; "manga" 4.1 →
# "mangar" 1.7. Real inflections are close: "pessoas" 6.2 → "pessoa" 5.6.
LEMMA_MAX_FREQUENCY_DROP = 1.0

# Lower-case letters (with Portuguese accents), optionally hyphenated: "guarda-chuva".
WORD_RE = re.compile(r"^[a-zà-öø-ÿ]{2,}(?:-[a-zà-öø-ÿ]{2,})*$")


# ---------------------------------------------------------------------------
# Word list
# ---------------------------------------------------------------------------


def read_list(path: Path) -> list[str]:
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def read_extras(path: Path) -> dict[str, list[str]]:
    """'word | variant, variant' lines → {word: [variants]}."""
    extras: dict[str, list[str]] = {}
    for line in read_list(path):
        word, _, variants = line.partition("|")
        word = normalize(word)
        extras[word] = [normalize(v) for v in variants.split(",") if v.strip()]
    return extras


def normalize(raw: str) -> str:
    """Same normalisation the app applies to guesses (app.games.ranking.normalize_word)."""
    return " ".join(raw.lower().split())


def read_overrides(path: Path) -> dict[str, str]:
    """'form = lemma' lines; 'word = word' keeps a word as its own entry."""
    overrides = {}
    for line in read_list(path):
        form, _, lemma = line.partition("=")
        overrides[normalize(form)] = normalize(lemma)
    return overrides


def lemma_of(word: str, overrides: dict[str, str]) -> str:
    if word in overrides:
        return overrides[word]
    lemma = simplemma.lemmatize(word, lang="pt")
    if lemma == word or not WORD_RE.match(lemma):
        return word
    drop = zipf_frequency(word, "pt") - zipf_frequency(lemma, "pt")
    return lemma if drop <= LEMMA_MAX_FREQUENCY_DROP else word


@dataclass
class Dictionary:
    base_forms: set[str]  # lower-case entries: common words in their base form
    proper_nouns: set[str]  # capitalised entries, lower-cased: names and places


def load_dictionary() -> Dictionary:
    CACHE.mkdir(exist_ok=True)
    path = CACHE / "pt_BR.dic"
    if not path.exists():
        print(f"Baixando o dicionário pt-BR de {DICTIONARY_URL} ...")
        response = httpx.get(DICTIONARY_URL, timeout=60, follow_redirects=True)
        response.raise_for_status()
        path.write_bytes(response.content)

    base, proper = set(), set()
    lines = path.read_text(encoding="utf-8-sig").splitlines()[1:]  # first line: entry count
    for line in lines:
        entry = line.split("/", 1)[0].strip()
        if not entry:
            continue
        if entry[0].isupper():
            proper.add(entry.lower())
        else:
            base.add(entry)
    return Dictionary(base, proper)


@dataclass
class WordList:
    words: list[str] = field(default_factory=list)  # vocabulary, most frequent first
    forms: dict[str, str] = field(default_factory=dict)  # typed form → vocabulary word
    dropped: dict[str, int] = field(default_factory=dict)  # reason → count


def build_word_list(size: int, candidates: int) -> WordList:
    dictionary = load_dictionary()
    stopwords = set(read_list(DATA / "stopwords_pt.txt"))
    blocklist = set(read_list(DATA / "blocklist_pt.txt"))
    extras = read_extras(DATA / "extra_words.txt")
    overrides = read_overrides(DATA / "lemma_overrides.txt")

    result = WordList()
    seen: set[str] = set()

    def drop(reason: str) -> None:
        result.dropped[reason] = result.dropped.get(reason, 0) + 1

    def add(word: str) -> None:
        if word not in seen:
            seen.add(word)
            result.words.append(word)

    for word, variants in extras.items():
        add(word)
        for variant in variants:
            if variant != word:
                result.forms[variant] = word

    for candidate in top_n_list("pt", candidates, wordlist="large"):
        if len(result.words) >= size:
            break
        if not WORD_RE.match(candidate):
            drop("não é palavra (números, símbolos, 1 letra)")
            continue
        if candidate in stopwords:
            drop("stopword")
            continue
        if not simplemma.is_known(candidate, lang="pt"):
            drop("fora do dicionário (inglês, gírias, erros)")
            continue
        if candidate in dictionary.proper_nouns and candidate not in dictionary.base_forms:
            drop("nome próprio")
            continue

        lemma = lemma_of(candidate, overrides)

        if lemma in blocklist or candidate in blocklist:
            drop("termo ofensivo")
            continue
        if lemma in stopwords:
            drop("stopword")
            continue

        add(lemma)
        if candidate != lemma and candidate not in result.forms:
            result.forms[candidate] = lemma

    vocabulary = set(result.words)
    # A form must point to a kept word and must not itself be a vocabulary word.
    result.forms = {
        form: word
        for form, word in result.forms.items()
        if word in vocabulary and form not in vocabulary
    }
    return result


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------


def _cache_path(model_version: str) -> Path:
    return CACHE / f"embeddings-{model_version}.npz"


def load_cached_embeddings(model_version: str) -> dict[str, np.ndarray]:
    path = _cache_path(model_version)
    if not path.exists():
        return {}
    data = np.load(path, allow_pickle=False)
    return dict(zip(data["words"].tolist(), data["vectors"], strict=True))


def save_cached_embeddings(model_version: str, cache: dict[str, np.ndarray]) -> None:
    CACHE.mkdir(exist_ok=True)
    words = list(cache)
    np.savez_compressed(
        _cache_path(model_version),
        words=np.array(words),
        vectors=np.stack([cache[w] for w in words]).astype(np.float16),
    )


def embed_batch(http: httpx.Client, api_key: str, texts: list[str]) -> list[np.ndarray]:
    for attempt in range(6):
        response = http.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": OPENAI_MODEL, "input": texts, "dimensions": DIMENSIONS},
        )
        if response.status_code == 200:
            data = sorted(response.json()["data"], key=lambda item: item["index"])
            return [np.asarray(item["embedding"], dtype=np.float32) for item in data]
        if response.status_code == 429 and "insufficient_quota" in response.text:
            sys.exit(
                "A conta da OpenAI está sem créditos. Adicione créditos em "
                "https://platform.openai.com/settings/organization/billing e rode de novo."
            )
        if response.status_code in (429, 500, 502, 503, 504):
            wait = 2**attempt
            print(f"  OpenAI respondeu {response.status_code}; nova tentativa em {wait}s")
            time.sleep(wait)
            continue
        sys.exit(f"Erro da OpenAI ({response.status_code}): {response.text[:300]}")
    sys.exit("A OpenAI continuou recusando as requisições; tente mais tarde.")


def read_embedding_texts(path: Path) -> dict[str, str]:
    """'word | text' lines → {word: text sent to the model in place of the word}."""
    texts: dict[str, str] = {}
    for line in read_list(path):
        word, _, text = line.partition("|")
        texts[normalize(word)] = text.strip()
    return texts


def embed_words(words: list[str], model_version: str, batch_size: int) -> dict[str, np.ndarray]:
    """Vectors for `words`. The cache is keyed by the text actually embedded."""
    overrides = read_embedding_texts(DATA / "embedding_texts.txt")
    text_of = {w: overrides.get(w, w) for w in words}
    cache = load_cached_embeddings(model_version)
    missing = list(dict.fromkeys(t for t in text_of.values() if t not in cache))
    print(f"Embeddings: {len(words) - len(missing)} em cache, {len(missing)} a gerar.")
    if missing:
        api_key = os.environ.get("OPENAI_API_KEY") or _env_file_value("OPENAI_API_KEY")
        if not api_key:
            sys.exit("Defina OPENAI_API_KEY no .env para gerar os embeddings.")
        with httpx.Client(timeout=120) as http:
            for start in range(0, len(missing), batch_size):
                batch = missing[start : start + batch_size]
                for text, vector in zip(batch, embed_batch(http, api_key, batch), strict=True):
                    cache[text] = vector
                save_cached_embeddings(model_version, cache)  # progress survives a crash
                print(f"  {min(start + batch_size, len(missing))}/{len(missing)}")
    return {w: cache[text_of[w]] for w in words}


def _env_file_value(name: str) -> str | None:
    env = ROOT.parent / ".env"
    if not env.exists():
        return None
    for line in env.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if key.strip() == name:
            return value.strip() or None
    return None


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


async def write_database(
    model_version: str, vectors: dict[str, np.ndarray], forms: dict[str, str]
) -> None:
    engine = create_async_engine(get_settings().database_url)
    async with engine.begin() as conn:
        await conn.execute(delete(WordForm).where(WordForm.model_version == model_version))
        await conn.execute(delete(Vocabulary).where(Vocabulary.model_version == model_version))
        rows = [
            {"model_version": model_version, "word": word, "embedding": vector}
            for word, vector in vectors.items()
        ]
        for start in range(0, len(rows), 1000):
            await conn.execute(insert(Vocabulary), rows[start : start + 1000])
        if forms:
            await conn.execute(
                insert(WordForm),
                [{"model_version": model_version, "form": f, "word": w} for f, w in forms.items()],
            )
    await engine.dispose()


# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model-version", default=DEFAULT_MODEL_VERSION)
    parser.add_argument("--size", type=int, default=50_000, help="palavras no vocabulário")
    parser.add_argument(
        "--candidates", type=int, default=267_000, help="palavras frequentes a examinar"
    )
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument(
        "--dry-run", action="store_true", help="só monta e mostra a lista, sem OpenAI nem banco"
    )
    args = parser.parse_args()

    word_list = build_word_list(args.size, args.candidates)
    print(f"Vocabulário: {len(word_list.words)} palavras, {len(word_list.forms)} formas.")
    for reason, count in sorted(word_list.dropped.items(), key=lambda item: -item[1]):
        print(f"  descartadas por {reason}: {count}")

    if args.dry_run:
        words = word_list.words
        print("Amostra (início):", words[:20])
        print("Amostra (meio):", words[len(words) // 2 : len(words) // 2 + 20])
        print("Amostra (fim):", words[-20:])
        print("Formas:", list(word_list.forms.items())[:20])
        return

    vectors = embed_words(word_list.words, args.model_version, args.batch_size)
    asyncio.run(write_database(args.model_version, vectors, word_list.forms))
    print(f"Pronto: vocabulário '{args.model_version}' gravado no banco.")


if __name__ == "__main__":
    main()
