FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libraqm0 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
RUN useradd --create-home --uid 1000 instapost
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN uv sync --frozen --no-dev && mkdir -p state output secrets && chown -R instapost /app
USER instapost
ENV PATH="/app/.venv/bin:$PATH"
CMD ["instapost", "worker"]
