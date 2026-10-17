# InstaPost

A self-hosted content agent for a Hindi spiritual and peace-of-mind page (Shiva, Krishna/Gita,
Kabir, life lessons) on Instagram, Facebook Reels and YouTube Shorts.

- Product and technical plan: [`docs/PLAN.md`](docs/PLAN.md)
- Build prompt for the full agent: [`docs/AGENT_BUILD_PROMPT.md`](docs/AGENT_BUILD_PROMPT.md)

## Status

- **Phase 1 – Studio:** creates finished, QA-checked content (below).
- **Phase 2 – Publishing, approvals, dashboard:** built. The worker creates tomorrow's content
  every evening, sends it to Telegram for approval, and publishes approved items on schedule
  to Instagram (reels, carousels, stories), Facebook Reels and YouTube Shorts. The admin
  dashboard shows live jobs, the approval queue, calendar, library and settings, with a kill
  switch. Deployment: [`docs/DEPLOY.md`](docs/DEPLOY.md).

- **Phase 3 – Stats:** built. The worker collects each post's numbers from Instagram, Facebook
  and YouTube at fixed ages (1 h, 3 h, 1/3/7/14/30 days; stories 1 h, 3 h, 20 h) plus follower
  counts every 6 hours, scores every post 0–100, and sends a daily report to Telegram at 09:00.
  The dashboard has a **Stats** page (follower growth, score by pillar and format, best posting
  times, all posts with sends/saves/watch) and a **Reports** page.

### Phase 3 at a glance

| Piece | What it does |
|---|---|
| Collector | Official insights APIs only; unsupported metric names are detected and skipped, numbers are never guessed |
| Post score | 0–100 from ratios: sends/reach 35%, saves/reach 25%, watch/length 25%, comments/reach 10%, follows/reach 5% (formula in `src/instapost/stats/score.py`) |
| Daily report | Followers and change, yesterday's posts, best 3 of the week, approvals waiting, spend, open setup items. Also `/report` in Telegram |
| Setup checklist | What is still needed from you: Settings page, `instapost check`, `/setup` in Telegram |

### Phase 2 at a glance

| Piece | What it does |
|---|---|
| `instapost worker` | Evening generation → Telegram; publishes due items; refreshes the Instagram token; runs dashboard requests |
| `instapost web` | Dashboard on 127.0.0.1 (behind Cloudflare Tunnel + Access), password + authenticator code |
| Telegram bot | ✅ Approve / ❌ Reject / 🔁 Regenerate per item, "approve next 3", "approve all of a day"; `/kill`, `/resume`, `/autonomy`, `/queue`, `/upcoming`, `/status` |
| Autonomy | L0 approve everything (default) · L1 auto stories + carousels · L2 auto everything. Items whose facts are unverified always wait for you |
| Safety | Kill switch, per-platform daily limits, one record per item and platform (never posts twice), crash recovery, tokens never in URLs or logs, audit log of every action |
| Schedule | `schedule.yaml`: slots, daily plan per weekday, pillar rotation, targets, limits |

## Phase 1: the studio

| Format | Output |
|---|---|
| `reel` | "Book page POV" reel: `reel.mp4` (1080×1920, H.264/AAC, 7–12 s, seamless loop), `cover.jpg` |
| `carousel` | Shlok + meaning carousel: `slide_01.jpg` … (1080×1350) |
| `story` | `story.jpg` (1080×1920) + `story_poll.txt` (poll stickers must be added in the app) |

Every item also gets `caption.txt` (Hindi + one Hinglish line + CTA + 3–5 hashtags),
`draft.json`, `fact_check.json`, `qa.json` and `status.json`.

### How one piece is made

1. **Writer** (Claude Sonnet 5.5) writes the hook, body, caption, hashtags and image prompts.
   It may only cite references from the verified-verse database (`data/scriptures/`).
2. **Safety rules** (free) block fear/chain messages, "type X in comments" bait, medical
   claims and politics.
3. **Fact check** (Claude Sonnet 5.5) checks attribution and meaning. The verse text printed on
   the post always comes from the database, never from the model.
4. **Images** (paid API, or the free offline placeholder): a background scene and a pencil
   sketch. The image model never draws text.
5. **Render**: our code draws all Hindi text with real fonts and correct conjunct shaping,
   then adds slow zoom, diya flicker, floating dust and an original tanpura-style drone.
6. **QA gate**: hook ≤ 12 words, text inside Instagram's safe area (clear of the like/share
   buttons), length matches reading time, seamless loop, visible motion, no near-duplicate
   of an earlier hook, optional vision check (Claude Haiku 5.5).

Failed items are saved with `status: blocked` and the reason. Budget caps ($0.60/day,
$18/month by default) are enforced before every paid call; see `instapost budget`.

## Setup

Full server setup: [`docs/DEPLOY.md`](docs/DEPLOY.md). Platform accounts and tokens:
[`docs/SETUP_META_YOUTUBE.md`](docs/SETUP_META_YOUTUBE.md).

## Usage

```bash
uv run instapost make reel --offline                 # free test run, no API keys needed
uv run instapost make reel --pillar gita --topic "गुस्सा" --vision-qa
uv run instapost make carousel --pillar kabir
uv run instapost make story --pillar life_lesson
uv run instapost make reel --draft my_draft.json     # render your own text
uv run instapost budget                              # spend vs caps
uv run instapost verses                              # verse database and review status
uv run instapost admin-setup                         # dashboard password + authenticator
uv run instapost web                                 # dashboard
uv run instapost worker                              # scheduler, publisher, Telegram bot
uv run instapost check                               # which platforms are connected
```

Pillars: `jeevan_sutra`, `shiv_dhyan`, `gita`, `kabir`, `life_lesson`.
Brand (page name, handle, fonts, colours, scene presets) lives in `brand.yaml`.

## Development

```bash
uv run pytest
uv run ruff check .
```

Fonts in `assets/fonts/` are under the SIL Open Font License (licence files alongside).
