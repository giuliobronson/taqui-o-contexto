# Production image: FastAPI app + migrations. The vocabulary pipeline is not
# included (it runs once from a developer machine against the database).
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.16 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /srv

# Dependencies first, so code changes do not reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-dev

COPY app ./app
COPY static ./static
COPY migrations ./migrations
COPY alembic.ini ./

RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8000

# --proxy-headers: behind Cloud Run/Render the client IP (rate limits) and the
# https scheme come from X-Forwarded-* headers set by the platform's proxy.
CMD ["sh", "-c", "exec /srv/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
