#!/usr/bin/env bash
# InstaPost on Docker: one script for everything. Run from the app folder as your normal user
# (a member of the docker group), e.g.:
#
#   bash deploy/docker.sh deploy            build + start (first time and after changes)
#   bash deploy/docker.sh deploy --proxy    same, reachable by nginx-proxy-manager (see docs)
#   bash deploy/docker.sh deploy --public   open on http://<server-ip>:<port> (no HTTPS!)
#   bash deploy/docker.sh deploy --local    back to private (SSH tunnel only, the default)
#   bash deploy/docker.sh update            latest code + rebuild (same as: bash update.sh)
#   bash deploy/docker.sh status            containers, port, setup checklist
#   bash deploy/docker.sh logs [worker|web] follow logs
#   bash deploy/docker.sh restart           after editing .env, brand.yaml, schedule.yaml, data/
#   bash deploy/docker.sh stop              stop everything (data is kept)
#   bash deploy/docker.sh admin-setup       create the dashboard login
#   bash deploy/docker.sh youtube-login     connect YouTube once (needs an SSH tunnel, see output)
#   bash deploy/docker.sh cli <args>        any instapost command, e.g. cli make reel --pillar gita
#   bash deploy/docker.sh verses review     go through the verses and mark them correct or wrong
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_ENV="$APP_DIR/deploy/compose.env"
PORT_RANGE_START=4010
PORT_RANGE_END=4099
cd "$APP_DIR"

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

compose() { docker compose --env-file "$COMPOSE_ENV" "$@"; }

preflight() {
  command -v docker >/dev/null || die "docker is not installed"
  docker compose version >/dev/null 2>&1 || die "docker compose v2 is missing"
  docker info >/dev/null 2>&1 || die "cannot talk to docker; add yourself to the docker group \
(sudo usermod -aG docker \$USER, then log out and in) or run with sudo"
}

# --- one-time housekeeping -----------------------------------------------------------------

take_ownership() {
  # Make the app folder yours, so the containers (running with your user id) and you can both
  # read and write it without sudo.
  local me; me="$(id -u):$(id -g)"
  if [ -n "$(find "$APP_DIR" -maxdepth 2 ! -user "$(id -u)" -print -quit 2>/dev/null)" ]; then
    say "Making $APP_DIR owned by $(id -un) (asks for your sudo password once)"
    sudo chown -R "$me" "$APP_DIR"
  fi
  mkdir -p state output secrets
  chmod 700 secrets
  if [ ! -f .env ]; then
    cp .env.example .env
    say "Created .env from .env.example"
  fi
  chmod 600 .env
}

retire_systemd() {
  # The earlier non-Docker install must not run at the same time.
  if [ -f /etc/systemd/system/instapost-worker.service ] || \
     [ -f /etc/systemd/system/instapost-web.service ]; then
    say "Removing the earlier systemd services (Docker replaces them)"
    sudo systemctl disable --now instapost-worker instapost-web 2>/dev/null || true
    sudo rm -f /etc/systemd/system/instapost-worker.service \
               /etc/systemd/system/instapost-web.service
    sudo systemctl daemon-reload || true
  fi
}

# --- port and bind address -------------------------------------------------------------------

port_busy() {
  local p="$1"
  if command -v ss >/dev/null; then
    ss -ltnH 2>/dev/null | awk '{print $4}' | grep -Eq "[:.]$p\$" && return 0
  fi
  # Fallback: is anything answering on this port?
  (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null && return 0
  docker ps --format '{{.Ports}}' | grep -Eq "[:.]$p->" && return 0
  return 1
}

own_port() {
  # The port our own web container already publishes (so a redeploy keeps it).
  docker ps --filter "name=^instapost-web-1$" --format '{{.Ports}}' \
    | grep -Eo ':[0-9]+->8800' | head -1 | sed -E 's/^:([0-9]+)->.*/\1/' || true
}

choose_port() {
  local saved="${WEB_PORT_HOST:-}" mine; mine="$(own_port)"
  if [ -n "$saved" ] && { [ "$saved" = "$mine" ] || ! port_busy "$saved"; }; then
    echo "$saved"; return
  fi
  for ((p = PORT_RANGE_START; p <= PORT_RANGE_END; p++)); do
    if ! port_busy "$p"; then echo "$p"; return; fi
  done
  die "no free port between $PORT_RANGE_START and $PORT_RANGE_END"
}

docker0_ip() {
  # Gateway of Docker's default bridge (usually 172.17.0.1): reachable by other containers
  # such as nginx-proxy-manager, never from the internet.
  docker network inspect bridge -f '{{range .IPAM.Config}}{{.Gateway}}{{end}}' 2>/dev/null \
    | head -1 || true
}

write_compose_env() {
  local mode="$1"  # keep | local | proxy | public
  # Keep earlier choices unless asked otherwise.
  if [ -f "$COMPOSE_ENV" ]; then
    # shellcheck disable=SC1090
    . "$COMPOSE_ENV"
  fi
  local bind="${WEB_BIND:-127.0.0.1}" trust="${FORWARDED_ALLOW_IPS:-*}"
  case "$mode" in
    local)  bind=127.0.0.1; trust="*" ;;
    proxy)
      bind="$(docker0_ip)"; trust="*"
      [ -n "$bind" ] || die "could not find Docker's bridge address (docker network inspect bridge)"
      ;;
    public) bind=0.0.0.0; trust=127.0.0.1 ;;
  esac
  local port; port="$(choose_port)"
  cat > "$COMPOSE_ENV" <<EOF
# Written by deploy/docker.sh. Settings for docker compose itself (not the app's keys).
APP_UID=$(id -u)
APP_GID=$(id -g)
WEB_BIND=$bind
WEB_PORT_HOST=$port
FORWARDED_ALLOW_IPS=$trust
EOF
  WEB_BIND="$bind"; WEB_PORT_HOST="$port"; FORWARDED_ALLOW_IPS="$trust"
}

server_ip() {
  hostname -I 2>/dev/null | awk '{print $1}'
}

check_disk() {
  # Images live where Docker keeps its data (usually the main disk), not on the app's volume.
  # The image needs about 1.2 GB, and up to twice that on newer Docker setups.
  local dir free_mb
  dir="$(docker info -f '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker)"
  free_mb=$(df -Pm "$dir" 2>/dev/null | awk 'NR==2 {print $4}')
  [ -n "$free_mb" ] || return 0
  if docker image inspect instapost:latest >/dev/null 2>&1; then
    return 0  # an update reuses most layers
  fi
  if [ "$free_mb" -lt 3500 ] && [ "${FORCE:-0}" != "1" ]; then
    echo "ERROR: only ${free_mb} MB free on the disk Docker uses ($dir)." >&2
    echo "Building InstaPost needs about 3500 MB free there, so your other containers stay safe." >&2
    echo "Options:" >&2
    echo "  1. See what can be freed:  docker system df" >&2
    echo "     Unused images:          docker image prune -a    (images no container uses)" >&2
    echo "     Build cache:            docker builder prune -a  (other projects rebuild slower once)" >&2
    echo "  2. Move Docker's storage to the volume: docs/DOCKER.md, section 'Low disk'" >&2
    echo "  3. Run anyway (not recommended): FORCE=1 bash deploy/docker.sh deploy" >&2
    exit 1
  fi
}

wait_healthy() {
  say "Waiting for the dashboard to answer"
  for _ in $(seq 1 40); do
    state=$(docker inspect -f '{{.State.Health.Status}}' instapost-web-1 2>/dev/null || true)
    if [ "$state" = "healthy" ]; then return 0; fi
    sleep 3
  done
  echo "The dashboard did not report healthy yet. Check: bash deploy/docker.sh logs web"
}

# --- commands --------------------------------------------------------------------------------

env_has() { grep -Eq "^$1=.+" .env 2>/dev/null; }

set_env() {
  # Replace KEY=... in .env, or append it. Values here never contain "|".
  local key="$1" value="$2"
  if grep -Eq "^#? *$key=" .env; then
    sed -i -E "s|^#? *$key=.*|$key=$value|" .env
  else
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}

cmd_admin_setup() {
  preflight
  [ -f "$COMPOSE_ENV" ] || die "run 'bash deploy/docker.sh deploy' first"
  local pw pw2 out
  read -rsp "New dashboard password (min 12 characters): " pw; echo
  read -rsp "Repeat: " pw2; echo
  [ "$pw" = "$pw2" ] || die "passwords do not match"
  [ "${#pw}" -ge 12 ] || die "password must be at least 12 characters"
  out=$(printf '%s\n' "$pw" | compose run --rm -T worker instapost admin-setup --password-stdin)
  unset pw pw2
  local key value totp="" uri=""
  while IFS='=' read -r key value; do
    case "$key" in
      SESSION_SECRET|ADMIN_PASSWORD_HASH) set_env "$key" "$value" ;;
      ADMIN_TOTP_SECRET) set_env "$key" "$value"; totp="$value" ;;
      TOTP_URI) uri="$value" ;;
    esac
  done <<< "$out"
  [ -n "$totp" ] || die "login could not be created"
  chmod 600 .env
  say "Login saved in .env (user: admin)"
  echo "Add it to Google Authenticator / Authy: Add account -> Enter a setup key:"
  echo "  key:  $totp"
  echo "  (type: time-based; or open on the phone: $uri)"
  say "Restarting the dashboard"
  compose up -d --force-recreate web
  wait_healthy
}

cmd_deploy() {
  local mode="keep"
  case "${1:-}" in
    --proxy)  mode=proxy ;;
    --public) mode=public ;;
    --local)  mode=local ;;
    "")       ;;
    *)        die "unknown option $1 (use --local, --proxy or --public)" ;;
  esac
  preflight
  take_ownership
  retire_systemd
  write_compose_env "$mode"
  if [ "$WEB_BIND" = "0.0.0.0" ]; then
    echo "WARNING: the dashboard will be open on http://$(server_ip):$WEB_PORT_HOST without HTTPS." >&2
    echo "         Password, 2FA code and login cookie travel unencrypted. Prefer a domain with" >&2
    echo "         HTTPS (docs/DOCKER.md); go back with: bash deploy/docker.sh deploy --local" >&2
  fi
  check_disk
  say "Building the image (first time: 3-6 minutes)"
  compose build
  say "Starting containers"
  compose up -d --remove-orphans
  docker image prune -f >/dev/null || true
  if env_has SESSION_SECRET && env_has ADMIN_PASSWORD_HASH && env_has ADMIN_TOTP_SECRET; then
    wait_healthy
  else
    say "The dashboard starts once a login exists. Next: bash deploy/docker.sh admin-setup"
  fi
  cmd_status
}

cmd_status() {
  preflight
  [ -f "$COMPOSE_ENV" ] && . "$COMPOSE_ENV"
  compose ps
  echo
  case "${WEB_BIND:-127.0.0.1}" in
    127.0.0.1)
      echo "Dashboard: private (SSH tunnel). From your Mac:"
      echo "  ssh -L ${WEB_PORT_HOST:-4010}:127.0.0.1:${WEB_PORT_HOST:-4010} $(id -un)@$(server_ip)"
      echo "  then open http://localhost:${WEB_PORT_HOST:-4010}" ;;
    0.0.0.0)
      echo "Dashboard: http://$(server_ip):${WEB_PORT_HOST} (public, no HTTPS)" ;;
    *)
      echo "Dashboard: for nginx-proxy-manager, forward to http://${WEB_BIND}:${WEB_PORT_HOST}" ;;
  esac
  echo
  compose exec -T worker instapost check 2>/dev/null | sed -n '/Setup checklist/,$p' || true
}

cmd_update() {
  # update.sh replaces this script during the update, so hand over instead of continuing here.
  exec bash "$APP_DIR/update.sh" "$@"
}

cmd_cli() {
  preflight
  [ -f "$COMPOSE_ENV" ] || die "run 'bash deploy/docker.sh deploy' first"
  # Show host paths instead of the container's /app/... paths.
  compose run --rm -T worker instapost "$@" | sed -u "s|/app/|$APP_DIR/|g"
}

cmd_youtube_login() {
  preflight
  [ -f "$COMPOSE_ENV" ] || die "run 'bash deploy/docker.sh deploy' first"
  [ -f secrets/youtube_client_secret.json ] || die "put the OAuth client JSON at \
$APP_DIR/secrets/youtube_client_secret.json first (docs/SETUP_META_YOUTUBE.md B2)"
  # No tunnel needed: you paste the address the browser ends up on (it contains the code).
  compose run --rm worker instapost youtube-login
}

cmd_verses() {
  preflight
  [ -f "$COMPOSE_ENV" ] || die "run 'bash deploy/docker.sh deploy' first"
  # data/ is read-only in the containers; only this command may change the verse files.
  compose run --rm -v "$APP_DIR/data/scriptures:/app/data/scriptures" worker \
    instapost verses "$@"
  case "${1:-list}" in
    review|set) say "Restarting the worker so it uses the updated verses"
                compose up -d --force-recreate worker ;;
  esac
}

case "${1:-deploy}" in
  deploy)      shift || true; cmd_deploy "$@" ;;
  update)      shift; cmd_update "$@" ;;
  status)      cmd_status ;;
  logs)        shift; preflight; compose logs -f --tail=100 "$@" ;;
  restart)     preflight; compose up -d --force-recreate; wait_healthy ;;
  stop)        preflight; compose down ;;
  admin-setup) cmd_admin_setup ;;
  youtube-login) cmd_youtube_login ;;
  cli)         shift; cmd_cli "$@" ;;
  verses)      shift; cmd_verses "$@" ;;
  *)           sed -n '2,18p' "$0"; exit 1 ;;
esac
