#!/usr/bin/env bash
# InstaPost installer. Run from inside the cloned repository:
#
#   sudo bash deploy/install.sh
#
# Everything (code, Python, packages, caches, videos, database, secrets) stays inside the
# repository folder, so you can put that folder on a mounted volume and keep the main disk free.
# Safe to run again after `git pull` (it updates packages and the services).
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER=instapost

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run with sudo: sudo bash deploy/install.sh" >&2
  exit 1
fi

echo "==> Installing into $APP_DIR"
echo "==> System packages (git, ffmpeg, libraqm for Hindi text)"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git ffmpeg libraqm0 curl ca-certificates

echo "==> Service user '$APP_USER' (no login shell)"
if ! id -u "$APP_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
fi
mkdir -p "$APP_DIR/state" "$APP_DIR/output" "$APP_DIR/secrets" "$APP_DIR/.uv"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 700 "$APP_DIR/secrets"

if ! sudo -u "$APP_USER" test -x "$APP_DIR"; then
  echo "ERROR: user '$APP_USER' cannot open $APP_DIR (a parent folder is not accessible)." >&2
  echo "Fix: sudo chmod o+x <each parent folder>, e.g. sudo chmod o+x $(dirname "$APP_DIR")" >&2
  exit 1
fi

run_as_app() {
  # Pass through proxy / certificate settings if this server uses them.
  local extra=()
  for v in HTTPS_PROXY HTTP_PROXY NO_PROXY https_proxy http_proxy no_proxy SSL_CERT_FILE; do
    [ -n "${!v:-}" ] && extra+=("$v=${!v}")
  done
  sudo -u "$APP_USER" env HOME="$APP_DIR" UV_CACHE_DIR="$APP_DIR/.uv/cache" \
    UV_PYTHON_INSTALL_DIR="$APP_DIR/.uv/python" PATH="$APP_DIR/.uv/bin:/usr/bin:/bin" \
    "${extra[@]}" "$@"
}

echo "==> uv (Python manager), kept inside $APP_DIR/.uv"
if [ ! -x "$APP_DIR/.uv/bin/uv" ]; then
  run_as_app bash -c 'set -euo pipefail
    curl -LsSf https://astral.sh/uv/install.sh -o "$HOME/.uv/install-uv.sh"
    env UV_INSTALL_DIR="$HOME/.uv/bin" INSTALLER_NO_MODIFY_PATH=1 sh "$HOME/.uv/install-uv.sh"'
fi

echo "==> Python packages"
run_as_app "$APP_DIR/.uv/bin/uv" sync --frozen --no-dev --project "$APP_DIR"

if [ ! -f "$APP_DIR/.env" ]; then
  echo "==> Creating .env from .env.example (fill in your keys next)"
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
fi
chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
chmod 600 "$APP_DIR/.env"

if [ -d /run/systemd/system ]; then
  echo "==> systemd services (installed, not started)"
  for name in instapost-web instapost-worker; do
    sed -e "s|/opt/instapost|$APP_DIR|g" "$APP_DIR/deploy/systemd/$name.service" \
      > "/etc/systemd/system/$name.service"
  done
  systemctl daemon-reload
fi

cat <<EOF

Done. Next:
  1. Edit your keys:       sudo -u $APP_USER nano $APP_DIR/.env
  2. Dashboard login:      sudo -u $APP_USER $APP_DIR/.venv/bin/instapost admin-setup
  3. First test reel:      cd $APP_DIR && sudo -u $APP_USER $APP_DIR/.venv/bin/instapost make reel --pillar gita --vision-qa
  4. Check setup:          sudo -u $APP_USER $APP_DIR/.venv/bin/instapost check
  5. Start services later: sudo systemctl enable --now instapost-worker instapost-web
EOF
