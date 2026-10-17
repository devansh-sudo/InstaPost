# Deploying InstaPost on your VPS

Two processes run on the server:

| Process | What it does |
|---|---|
| `instapost worker` | Creates the next day's content every evening, sends it to Telegram, publishes approved items on time, refreshes the Instagram token, runs requests from the dashboard |
| `instapost web` | The dashboard, bound to `127.0.0.1:8800` only. You reach it through Cloudflare Tunnel + Cloudflare Access |

Only the worker ever generates or publishes. The dashboard records decisions and queues requests.

Minimum server: 2 vCPU, 4 GB RAM, 20 GB disk, Ubuntu 22.04/24.04 or Debian 12.

---

## 1. Install (systemd, recommended)

```bash
sudo apt update && sudo apt install -y git ffmpeg libraqm0
sudo useradd --system --create-home --home-dir /opt/instapost --shell /usr/sbin/nologin instapost
sudo -u instapost git clone https://github.com/devansh-sudo/InstaPost /opt/instapost
cd /opt/instapost
sudo -u instapost bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh && ~/.local/bin/uv sync --no-dev'
sudo -u instapost mkdir -p state output secrets && sudo chmod 700 /opt/instapost/secrets
sudo -u instapost cp .env.example .env && sudo chmod 600 .env
```

(With Docker instead: `cp .env.example .env`, edit it, then `docker compose up -d --build`.)

## 2. Fill in `.env`

Edit with `sudo -u instapost nano /opt/instapost/.env`. Never paste these values anywhere else.

1. **Dashboard login:** run `sudo -u instapost .venv/bin/instapost admin-setup`. It asks for a
   password, then prints `SESSION_SECRET`, `ADMIN_PASSWORD_HASH` and `ADMIN_TOTP_SECRET`. Paste
   them into `.env`. Add the setup key to Google Authenticator or Authy on your phone.
2. **Claude + images:** `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` (and/or `OPENAI_API_KEY`).
3. **Meta + YouTube:** follow `docs/SETUP_META_YOUTUBE.md`.
4. **Telegram:**
   - In Telegram, open **@BotFather** → `/newbot` → pick a name → copy the token into `TELEGRAM_BOT_TOKEN`.
   - Start the worker once (step 4) and send your bot any message. It replies with your chat ID.
   - Put that number in `TELEGRAM_ADMIN_CHAT_ID` and restart the worker. The bot ignores everyone else.
5. **Media hosting (Cloudflare R2):** Instagram downloads videos from a link, so files need a
   temporary public URL.
   - Cloudflare dashboard → **R2** → Create bucket `instapost-media` (keep it private).
   - Bucket → **Settings → Object lifecycle rules** → delete objects after 7 days.
   - R2 → **Manage API tokens** → Create token, permission **Object Read & Write**, limited to
     that bucket. Copy the Access Key ID, Secret Access Key and your Account ID into
     `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ACCOUNT_ID`, and set `R2_BUCKET=instapost-media`.
   - Links the agent creates expire after 1 hour. R2's free tier covers this volume.

Check everything with `sudo -u instapost .venv/bin/instapost check`.

## 3. Try it without spending anything (optional)

Set `DEMO_MODE=true` in `.env`. The worker then uses sample text and drawn placeholder images,
with no Claude or image API calls, so you can test approvals, Telegram and the dashboard. Set it
back to `false` before going live.

## 4. Start the services

```bash
sudo cp deploy/systemd/instapost-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now instapost-worker instapost-web
journalctl -u instapost-worker -f      # live logs
```

## 5. Reach the dashboard safely (Cloudflare Tunnel + Access, free)

The dashboard is never exposed directly. Cloudflare Tunnel makes an outbound connection, so you
don't open any ports.

1. Add your domain to a free Cloudflare account (or buy one there).
2. Cloudflare dashboard → **Zero Trust** → **Networks → Tunnels** → Create tunnel (cloudflared)
   → name `instapost` → copy the install command it shows and run it on the VPS.
3. In the tunnel → **Public hostname** → `admin.yourdomain.com` → Service `http://localhost:8800`.
4. Zero Trust → **Access → Applications** → Add → Self-hosted → `admin.yourdomain.com` →
   policy **Allow**, rule **Emails: your email**. Cloudflare will email you a code before the page
   even loads. Then the dashboard asks for your password and authenticator code.
5. Set `PUBLIC_BASE_URL=https://admin.yourdomain.com` in `.env` (enables secure cookies) and
   restart the web service.

Firewall: allow only SSH. `sudo ufw default deny incoming && sudo ufw allow OpenSSH && sudo ufw enable`.
Use SSH keys and disable password login (`PasswordAuthentication no` in `/etc/ssh/sshd_config`).

## 6. Connect YouTube (one time)

From your laptop: `ssh -L 8085:localhost:8085 you@your-vps`, then on the VPS:
`sudo -u instapost .venv/bin/instapost youtube-login`. Open the printed link in your laptop's
browser, log in with the channel's Google account and approve. Done.

## 7. Daily life

- About **20:30** each evening the bot sends tomorrow's reels, carousels and stories to Telegram.
  Tap ✅ / ❌ / 🔁, or "📅 Approve all". Or use the **Approvals** page on the dashboard.
- Approved items post automatically at their slot. You get a 🚀 message with links.
- Stories: add the poll sticker in the app when the bot reminds you (the API can't add stickers).
- YouTube: until the API audit is approved, make each Short public in YouTube Studio (2 taps).
- About **09:00** the bot sends yesterday's report (followers, posts, best of the week). The
  **Stats** page has the charts; `/report` gives the report any time.
- `/setup` (or the Settings page) shows what is still missing in the setup.
- **Sunday 18:00:** Claude reviews the week (Telegram message), then next week's plan is built.
  Check it on the **Plan** page (or `/plan`) and change anything before the evening runs use it.
- **Emergency:** `/kill` in Telegram or the red **KILL SWITCH** button stops everything at once.

## Backups

```bash
# daily at 03:00: database + tokens, kept 14 days
echo '0 3 * * * instapost tar czf /opt/instapost/state/backup-$(date +\%F).tgz -C /opt/instapost state/instapost.db secrets && find /opt/instapost/state -name "backup-*" -mtime +14 -delete' | sudo tee /etc/cron.d/instapost-backup
```
