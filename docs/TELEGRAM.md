# Telegram bot setup (approvals, reports, kill switch)

The bot sends you every new post with ✅ Approve / ❌ Reject / 🔁 Regenerate buttons, a daily
report at 09:00, and accepts commands such as `/kill`. It only answers **your** Telegram
account (the chat ID in `.env`); everyone else is ignored.

Time: about 10 minutes. Cost: free.

## 1. Create the bot (on your phone)

1. Open Telegram, search **@BotFather** (blue tick), tap **Start**.
2. Send `/newbot`.
3. Name (shown in chats), e.g. `Thoughtory Agent`.
4. Username (must end in `bot`, unique), e.g. `thoughtory_agent_bot`.
5. BotFather replies with a **token** like `8123456789:AAH-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`.
   Treat it like a password: anyone with it controls the bot. Don't paste it in chats.

Recommended, in the same BotFather chat:
- `/setjoingroups` → choose your bot → **Disable** (nobody can add it to groups).
- `/setcommands` → choose your bot → paste the list below (gives you a tap-able menu):

```
new - create one now, e.g. /new reel gita गुस्सा
postnow - publish an item right away, e.g. /postnow <id>
posted - link a reel you posted in the Instagram app, e.g. /posted <id> <link>
queue - items waiting for approval
approve_next - approve the next N items, e.g. /approve_next 3
approve_tomorrow - approve everything planned for tomorrow
upcoming - what is scheduled
status - kill switch, autonomy, budget
plan - the plan for the next 7 days
report - yesterday's report now
budget - spend against the caps
setup - what is still missing in the setup
autonomy - L0, L1 or L2
kill - stop all publishing now
resume - allow publishing again
```

## 2. Put the token on the server

```bash
cd /mnt/HC_Volume_105724104/instapost
read -rsp "Paste bot token, then Enter: " T && echo
sed -i '/^TELEGRAM_BOT_TOKEN=/d' .env && echo "TELEGRAM_BOT_TOKEN=$T" >> .env && unset T
bash deploy/docker.sh restart
```

## 3. Get your chat ID

1. In Telegram, open your bot (search its username) and tap **Start** (or send `hi`).
2. Within a few seconds it replies: `Your chat ID is 123456789. Put it in TELEGRAM_ADMIN_CHAT_ID.`
   No reply? See "Troubleshooting".
3. Save it (a number, not secret, but only yours):

```bash
sed -i '/^TELEGRAM_ADMIN_CHAT_ID=/d' .env && echo "TELEGRAM_ADMIN_CHAT_ID=123456789" >> .env
bash deploy/docker.sh restart
```

The two lines in `.env` now look like this (example values):

```
TELEGRAM_BOT_TOKEN=8123456789:AAH-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TELEGRAM_ADMIN_CHAT_ID=123456789
```

## 4. Test

Send the bot `/status`, then `/setup`. On the server, `bash deploy/docker.sh cli check`
shows `telegram   set`. To see a real approval message, use the dashboard (Live → Create one
piece now) or wait for the evening run (`generate_at` in `schedule.yaml`, 20:30).

## Daily use

| You get | What to do |
|---|---|
| 🎬 Reel / 🖼 Carousel / 📱 Story with buttons | 🚀 post now (all platforms at once), ✅ approve (posts at its slot), 📲 trending song (reels: you post it on Instagram with a song from its app, the rest goes out now), ❌ reject, 🔁 regenerate |
| "📅 Approve all <date>" button | approves the whole day at once |
| 🚀 Published, with links | nothing; check the post if you like |
| 09:00 report | followers, yesterday's posts, best of the week, spend |
| ⚠️ / ⛔ messages | something failed or a cap was reached; the message says what |

Make something now: `/new` (a reel in today's pillar), `/new reel gita गुस्सा`,
`/new story shiv`, `/new carousel kabir`. On the server the same is
`bash deploy/docker.sh post reel gita गुस्सा`. It arrives here in 2–4 minutes.

Emergency: `/kill` stops all publishing at once; `/resume` allows it again.

## Troubleshooting

- **No reply to your message:** the worker must be running on the server:
  `bash deploy/docker.sh logs worker`. Look for `telegram` errors (e.g. `Unauthorized` = wrong
  token: create a new one with BotFather `/token`, update `.env`, restart).
- **Replies stopped / "Conflict: terminated by other getUpdates request" in the logs:** the same
  token is running in two places (e.g. `instapost worker` on your Mac). Only the server may run
  the worker with this token. For tests on the Mac, create a second bot.
- **Bot answers nothing after you set the chat ID:** the number must be your own ID from step 3
  (no spaces, no quotes). Commands from other accounts are ignored on purpose.
- **Token leaked:** BotFather → `/revoke` → choose the bot → put the new token in `.env`, restart.
