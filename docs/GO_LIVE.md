# Go-live checklist

Everything in the software is built (Phases 1–4). What's left is setup on your side, then a
careful first live run. Do the steps in this order. Total time: about 3–5 hours spread over a
few days (Meta and Google screens take the longest).

**Rule for every step:** keys, tokens and passwords go only into `/opt/instapost/.env` (or
`secrets/`) on your VPS. Never into chat, email, screenshots or git.

---

## Step 1 — Accounts (≈30 min) · guide: `docs/SETUP_META_YOUTUBE.md` A1, B1
- [ ] Facebook: turn on two-factor authentication.
- [ ] Facebook Page created for the brand.
- [ ] Instagram switched to **Professional → Creator**, linked to the Page.
- [ ] YouTube channel created on the brand's Google account and phone-verified (youtube.com/verify).
- [ ] Start using the accounts normally from your phone (follow a few similar pages, post 1–2
      things by hand). Brand-new accounts that only post through an API look like bots.

## Step 2 — Paid API keys (≈20 min)
- [ ] **Claude:** console.anthropic.com → sign up → Billing: add about $10 credit → **set a
      monthly spend limit of $20** (second safety net next to the agent's own $18 cap) →
      API keys → Create key. Copy it once; it goes into `ANTHROPIC_API_KEY`.
- [ ] **Gemini (images):** aistudio.google.com → Get API key → create key. Image generation needs
      billing turned on for that key's Google Cloud project (AI Studio shows a "Set up billing"
      link). Set a budget alert of $10 in Google Cloud → Billing → Budgets. Key goes into
      `GEMINI_API_KEY`.
- [ ] Optional: OpenAI key (`OPENAI_API_KEY`) if you want to compare image quality.

## Step 3 — Telegram bot (≈5 min)
- [ ] Telegram → **@BotFather** → `/newbot` → name it → copy the token (`TELEGRAM_BOT_TOKEN`).
- [ ] Your chat ID comes later (Step 6): the bot tells you when you message it.

## Step 4 — Cloudflare (≈20 min) · guide: `docs/DEPLOY.md` steps 2.5 and 5
- [ ] Free Cloudflare account.
- [ ] **R2:** create bucket `instapost-media` (private) → lifecycle rule "delete after 7 days" →
      API token with Object Read & Write on that bucket → note Account ID, Access Key ID, Secret.
- [ ] A domain (optional but recommended, about ₹800–1,200 a year), added to Cloudflare.

## Step 5 — Install on the VPS (≈30 min) · guide: `docs/DEPLOY.md` step 1
- [ ] Ubuntu 22.04/24.04 or Debian 12, 2 vCPU / 4 GB RAM minimum.
- [ ] Clone and run `sudo bash deploy/install.sh` (DEPLOY.md step 1). On a small main disk, install
      on the mounted volume.
- [ ] `sudo -u instapost .venv/bin/instapost admin-setup` → paste the 3 printed lines into `.env`
      → add the setup key to Google Authenticator / Authy.
- [ ] Firewall: only SSH open; SSH keys only (DEPLOY.md step 5, last part).

## Step 6 — Trial run in demo mode (free, ≈20 min)
- [ ] In `.env`: `DEMO_MODE=true`, `TELEGRAM_BOT_TOKEN=...`.
- [ ] Start services: `sudo systemctl enable --now instapost-worker instapost-web`.
- [ ] Message your bot "hi" → it replies with your chat ID → put it in
      `TELEGRAM_ADMIN_CHAT_ID` → `sudo systemctl restart instapost-worker`.
- [ ] Dashboard via SSH tunnel for now: `ssh -L 8800:localhost:8800 you@vps`, open
      http://localhost:8800 on your laptop, log in with password + authenticator code.
- [ ] Live page → "Create one piece now" → story → it arrives in Telegram with ✅/❌ buttons.
- [ ] Try `/status`, `/setup`, `/plan`, the KILL SWITCH and Resume.

## Step 7 — Content setup (≈1 hour, only you can do this)
- [ ] `brand.yaml`: set `handle:` (e.g. `@jeevan.sutra`) and `page_name:` to the real ones.
- [ ] **Verses:** open `data/scriptures/gita.yaml`, `kabir.yaml`, `shiv.yaml`; compare every
      `text:` with a printed Gita Press Gita / trusted Kabir and Shiva sources; fix any difference;
      set `review_status: reviewed`. (17 entries. Until reviewed, every post quoting them is
      flagged for your manual check.) Add more verses the same way if you like.
- [ ] **Festivals:** check every date in `data/calendar/festivals.yaml` on drikpanchang.com,
      fix it, set `confirmed: true`. Add Sawan Somvar, Pradosh, Ekadashi as you wish.
- [ ] Restart both services after editing these files.

## Step 8 — Connect Instagram, Facebook, YouTube (≈1–2 hours) · guide: `docs/SETUP_META_YOUTUBE.md`
- [ ] Meta developer app (development mode is enough), privacy-policy URL.
- [ ] Instagram token + user ID → `IG_ACCESS_TOKEN`, `IG_USER_ID`; also `META_APP_ID`, `META_APP_SECRET`.
- [ ] Facebook Page ID + never-expiring Page token → `FB_PAGE_ID`, `FB_PAGE_TOKEN`.
- [ ] Google Cloud project: YouTube Data API + YouTube Analytics API enabled, consent screen
      **In production**, Desktop OAuth client JSON → `secrets/youtube_client_secret.json`.
- [ ] `instapost youtube-login` through the SSH tunnel (DEPLOY.md step 6).
- [ ] Submit the **YouTube API Services Audit** form (takes weeks; until approved, Shorts upload
      as private and you tap "Public" in YouTube Studio).
- [ ] R2 values into `.env` (`R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`).

## Step 9 — Secure dashboard address (≈20 min) · guide: `docs/DEPLOY.md` step 5
- [ ] Cloudflare Tunnel → `admin.yourdomain.com` → `http://localhost:8800`.
- [ ] Cloudflare Access policy: only your email.
- [ ] `PUBLIC_BASE_URL=https://admin.yourdomain.com` in `.env`, restart web.

## Step 10 — Go live, carefully (≈30 min)
- [ ] `.env`: `DEMO_MODE=false`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` set → restart both services.
- [ ] `sudo -u instapost .venv/bin/instapost check` → every required item says done.
      **Send me this output** (it contains no secrets).
- [ ] Dashboard → Live → create **one** reel for tomorrow. Look at it carefully in Approvals
      (Hindi text, verse, caption, image quality).
- [ ] Approve it → "🚀 Post now" → confirm it appears on Instagram, Facebook and YouTube
      (YouTube as private). If anything fails, the item page shows the error: **send me the
      error text** (tokens are already removed from it).
- [ ] Keep autonomy at **L0** (you approve everything) for the first 2–3 weeks.

## Step 11 — First weeks
- Evenings ~20:30: approve tomorrow's pieces in Telegram (or "📅 Approve all").
- Mornings 09:00: read the daily report. Sundays 18:00: weekly review + plan; check the Plan page.
- Stories: add the poll sticker in the app when reminded. YouTube: make Shorts public until the audit passes.
- After ~30–50 posts the Learning page becomes meaningful. Week 3+: consider autonomy L1.
- Week 1: at most 1 post per platform per day (lower `max_posts_per_day` in `schedule.yaml`).

---

## What is still open on the development side
| Item | Why it's open | When |
|---|---|---|
| First live run against the real Meta / YouTube / Gemini / Claude APIs | Everything was tested with simulated servers; real APIs may differ in small ways (field names, limits) | Fixed together during Step 10 |
| Instagram/Facebook/YouTube stat names | Metric names change between API versions; the collector skips unknown ones and reports them | Checked after the first posts are 1 day old |
| Image provider bake-off | Choose Gemini vs OpenAI by real image quality (5 test images each) | After Step 2 |
| Batch API (half-price writing) | Saves ~$2–3/month; bigger rework | Only if spend nears the cap |
| Comment replies, trending audio | Not built: auto-replies risk spam rules; trending audio is impossible through the API | By decision |
