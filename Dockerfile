# InstaPost image (used by docker-compose.yml for both the worker and the dashboard).
# Data never lives in the image: state/, output/ and secrets/ are bind-mounted from the host.
# Ubuntu 24.04 base: Python 3.12, ffmpeg and libraqm (Hindi text shaping) from the same
# packages the app was tested with.
FROM ubuntu:24.04

# The container user gets the same numeric id as the owner of the host data folders, so
# files written into the bind mounts stay readable on the host (set by deploy/docker.sh).
ARG APP_UID=1000
ARG APP_GID=1000

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Kolkata \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=python3.12 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       python3.12 python3-pip ffmpeg libraqm0 tzdata ca-certificates \
    && pip3 install --no-cache-dir --break-system-packages uv==0.11.32 \
    && apt-get purge -y python3-pip && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* /root/.cache \
    && (getent group "$APP_GID" || groupadd -g "$APP_GID" instapost) \
    && (getent passwd "$APP_UID" || useradd -u "$APP_UID" -g "$APP_GID" -M -d /app \
        -s /usr/sbin/nologin instapost) \
    && mkdir -p /app && chown "$APP_UID:$APP_GID" /app

# Everything below runs as the app user, so no extra "chown" layer duplicates the packages.
USER ${APP_UID}:${APP_GID}
WORKDIR /app
COPY --chown=${APP_UID}:${APP_GID} pyproject.toml uv.lock ./
# The Google API library ships discovery files for every Google service (~100 MB); only the
# two YouTube ones are used. Removed in the same step, so they never take space in the image.
RUN uv sync --frozen --no-dev --no-install-project \
    && find .venv/lib/python3.12/site-packages/googleapiclient/discovery_cache/documents \
       -name '*.json' ! -name 'youtube.v3.json' ! -name 'youtubeAnalytics.v2.json' -delete
COPY --chown=${APP_UID}:${APP_GID} . .
RUN uv sync --frozen --no-dev && mkdir -p state output secrets

ENV PATH="/app/.venv/bin:$PATH"
CMD ["instapost", "worker"]
