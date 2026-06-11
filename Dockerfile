# Clean, paper-only worker image for Railway.
# Uses the official uv base image — no `curl | bash`, no remote install scripts.
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

WORKDIR /app

# Install dependencies first (better layer caching).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

# App code.
COPY hermes_trading ./hermes_trading
RUN uv sync --frozen

# Default strategy/goal are baked here and seeded into the persistent volume
# (mounted at /app/state) on first boot by run.py's bootstrap step. This keeps
# the evolving state on the volume while shipping sane defaults in the image.
COPY state ./seed_state

# Paper mode is the only supported mode in this image. There is no live adapter.
ENV HERMES_TRADING_MODE=paper \
    PATH="/app/.venv/bin:${PATH}"

CMD ["uv", "run", "python", "-m", "hermes_trading.run"]
