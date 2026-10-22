# Running InstaPost with Docker

Everything goes through one script, `deploy/docker.sh`, run from the app folder as your normal
user (who must be in the `docker` group; check with `docker ps`).

| What | Where |
|---|---|
| App folder (code, `.env`, data) | `/mnt/HC_Volume_105724104/instapost` (the mounted volume) |
| Database, logs of jobs, spend | `state/` in that folder |
| Generated reels, carousels, stories | `output/` |
| Tokens (Instagram refresh, YouTube login) | `secrets/` |
| Settings you edit (restart after editing) | `.env`, `brand.yaml`, `schedule.yaml`, `data/` |
| Docker image (~1.2 GB) | Docker's own storage, usually the main disk (`/var/lib/docker`) |
| Containers | `instapost-worker-1` (does all the work), `instapost-web-1` (dashboard) |
| Dashboard port | first free port from 4010 (saved in `deploy/compose.env`); private unless you choose `--public` |

## Commands

```bash
cd /mnt/HC_Volume_105724104/instapost
bash deploy/docker.sh deploy            # build + start (first time and after code changes)
bash deploy/docker.sh deploy --proxy    # same, reachable by nginx-proxy-manager
bash deploy/docker.sh deploy --public   # same, open on http://<server-ip>:<port> (no HTTPS)
bash deploy/docker.sh deploy --local    # back to private (SSH tunnel only)
bash deploy/docker.sh admin-setup       # create the dashboard login (password + 2FA)
bash deploy/docker.sh status            # containers, port, setup checklist
bash deploy/docker.sh logs              # live logs (Ctrl+C to leave); "logs worker" / "logs web"
bash deploy/docker.sh restart           # after editing .env, brand.yaml, schedule.yaml, data/
bash update.sh                          # latest code: backup + rebuild + restart (see below)
bash deploy/docker.sh stop              # stop everything (data is kept)
bash deploy/docker.sh cli <command>     # any instapost command, e.g.:
bash deploy/docker.sh cli make reel --pillar gita --vision-qa
bash deploy/docker.sh cli check
bash deploy/docker.sh youtube-login     # connect YouTube once (needs an SSH tunnel; it explains)
```

## Your own pictures (free image source)

Copy pictures from your Mac into the server's library (see `data/images/README.md` for naming):
```bash
scp ~/Downloads/shiva-*.jpg devansh@46.224.187.54:/mnt/HC_Volume_105724104/instapost/data/images/subjects/
scp ~/Downloads/diya-*.jpg  devansh@46.224.187.54:/mnt/HC_Volume_105724104/instapost/data/images/scenes/
```
No restart needed; `bash deploy/docker.sh cli check` shows how many it found.

## Updating to the latest code

```bash
cd /mnt/HC_Volume_105724104/instapost && bash update.sh
```
It fetches the new code and lists what changed, backs up the database to `state/backups/`
(keeps the last 5), rebuilds and restarts, then shows the status. Your edits to
`brand.yaml`, `schedule.yaml` and `data/` are kept; if the new code changes the same file, it
stops without touching anything and tells you which file. If the build fails, the running
version keeps running. Options: `--force` (rebuild without new code), `--public`/`--local`/
`--proxy` (change how the dashboard is reached).

## First deployment, step by step

1. **Check disk space where Docker keeps images** (the build needs about 3.5 GB free there):
   ```bash
   docker info -f '{{.DockerRootDir}} {{.Driver}}'
   df -h "$(docker info -f '{{.DockerRootDir}}')"
   docker system df
   ```
   If space is short, see "Low disk" below. The script also checks and stops safely.

2. **Make the folder yours and remove the earlier systemd install** (the script does both;
   it asks for your sudo password once):
   ```bash
   cd /mnt/HC_Volume_105724104/instapost
   git pull
   bash deploy/docker.sh deploy
   ```
   The first build takes 3–6 minutes. At the end it says the dashboard starts once a login exists.

3. **Keys:** `nano .env` → fill `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`.
   If a value contains a `$`, put it in single quotes: `KEY='va$lue'`.

4. **Login:** `bash deploy/docker.sh admin-setup` → choose a password → add the shown key to
   Google Authenticator / Authy. It writes the login into `.env` and starts the dashboard.

5. **Restart to load the keys:** `bash deploy/docker.sh restart`

6. **First real reel (≈ $0.15):**
   `bash deploy/docker.sh cli make reel --pillar gita --vision-qa`
   Copy it to your Mac (run on the Mac, using the printed folder):
   `scp devansh@46.224.187.54:/mnt/HC_Volume_105724104/instapost/output/<date>/<id>/reel.mp4 ~/Desktop/`

7. **Dashboard from your Mac** (SSH tunnel; port from `status`):
   `ssh -L 4010:127.0.0.1:4010 devansh@46.224.187.54` → open http://localhost:4010

8. **Telegram:** message your bot; it replies with your chat ID → put it in
   `TELEGRAM_ADMIN_CHAT_ID` in `.env` → `bash deploy/docker.sh restart`.

## Dashboard on a domain with nginx-proxy-manager (already running on this server)

This also removes the need for Cloudflare R2: Instagram can fetch videos from your own domain
through short-lived signed links.

1. DNS: point `insta.yourdomain.com` (A record) to `46.224.187.54`.
2. `bash deploy/docker.sh deploy --proxy` → the dashboard now listens on Docker's bridge
   address (e.g. `172.17.0.1:4010`), reachable by nginx-proxy-manager but not from the internet.
3. nginx-proxy-manager (`http://46.224.187.54:81` via SSH tunnel is safer:
   `ssh -L 8181:127.0.0.1:81 ...`) → Hosts → Proxy Hosts → Add:
   - Domain: `insta.yourdomain.com` · Scheme `http` · Forward host `172.17.0.1` · port `4010`
   - SSL tab: request a Let's Encrypt certificate, Force SSL, HTTP/2.
   - Do **not** add an Access List: Instagram must be able to fetch `/media/...` links. The
     dashboard itself is protected by password + 2FA, rate-limited logins and CSRF checks.
4. In `.env`: `PUBLIC_BASE_URL=https://insta.yourdomain.com` and `MEDIA_HOST=local` →
   `bash deploy/docker.sh restart`.

## Dashboard on the server IP (quick, no HTTPS)

```bash
bash deploy/docker.sh deploy --public
bash deploy/docker.sh status        # prints http://<server-ip>:<port>
```
- If your provider has a cloud firewall (Hetzner Console → Firewalls), allow inbound TCP on
  that port. Docker-published ports bypass `ufw`.
- Plain HTTP: your password and 2FA code travel unencrypted, so anyone on the network path
  (public Wi-Fi, for example) could read them. 2FA limits the damage, but use the domain +
  HTTPS setup above as soon as you can. In this mode the login limiter ignores
  `X-Forwarded-For`, so nobody can fake their IP to get extra password attempts.
- Back to private: `bash deploy/docker.sh deploy --local`.

## Low disk (main disk nearly full)

Docker stores images on the main disk by default. Options, safest first:
1. Free unused space: `docker system df`, then `docker image prune -a` (removes images no
   container uses) and `docker builder prune -a` (build cache; other projects rebuild slower once).
2. Move Docker's storage to the volume. **This stops all containers on the server for a few
   minutes** (echohire, portfolio, etc.), so do it at a quiet time:
   ```bash
   sudo systemctl stop docker docker.socket
   sudo rsync -aHAX /var/lib/docker/ /mnt/HC_Volume_105724104/docker/
   sudo mkdir -p /etc/systemd/system/docker.service.d
   printf '[Unit]\nRequiresMountsFor=/mnt/HC_Volume_105724104\n' | \
     sudo tee /etc/systemd/system/docker.service.d/volume.conf
   # add "data-root" to /etc/docker/daemon.json (create it if missing; merge if it exists):
   #   { "data-root": "/mnt/HC_Volume_105724104/docker" }
   sudo nano /etc/docker/daemon.json
   sudo systemctl daemon-reload && sudo systemctl start docker
   docker ps        # all your containers should be back
   ```
   Once everything works for a day: `sudo rm -rf /var/lib/docker.old` (after
   `sudo mv /var/lib/docker /var/lib/docker.old` while Docker is stopped, if you want a backup).

## Security notes for this server
- The dashboard port is bound to `127.0.0.1` (or the Docker bridge with `--proxy`), and to
  the internet only if you choose `--public`. Ports that Docker publishes on `0.0.0.0` bypass `ufw`.
- Your MongoDB container publishes `0.0.0.0:27017` (open to the internet). Unless something
  outside needs it, change its port mapping to `127.0.0.1:27017:27017` and make sure it has a
  password.
- Use SSH keys and turn off password login once you can log in with a key.
