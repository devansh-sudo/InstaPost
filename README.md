# InstaPost

A self-hosted content agent for a Hindi spiritual and peace-of-mind page (Shiva, Krishna/Gita,
Kabir, life lessons) on Instagram, Facebook Reels and YouTube Shorts.

- Product and technical plan: [`docs/PLAN.md`](docs/PLAN.md)
- Build prompt for the full agent: [`docs/AGENT_BUILD_PROMPT.md`](docs/AGENT_BUILD_PROMPT.md)

## Status: Phase 1 (Studio MVP)

Phase 1 creates finished, QA-checked content as files. You review them and post by hand.
Publishing, Telegram approvals and the dashboard come in Phase 2.

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

## Setup (on the VPS)

```bash
sudo apt install ffmpeg libraqm0          # libraqm = correct Hindi text shaping
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/devansh-sudo/InstaPost && cd InstaPost
uv sync
cp .env.example .env && chmod 600 .env    # then add your keys with a text editor
```

## Usage

```bash
uv run instapost make reel --offline                 # free test run, no API keys needed
uv run instapost make reel --pillar gita --topic "गुस्सा" --vision-qa
uv run instapost make carousel --pillar kabir
uv run instapost make story --pillar life_lesson
uv run instapost make reel --draft my_draft.json     # render your own text
uv run instapost budget                              # spend vs caps
uv run instapost verses                              # verse database and review status
```

Pillars: `jeevan_sutra`, `shiv_dhyan`, `gita`, `kabir`, `life_lesson`.
Brand (page name, handle, fonts, colours, scene presets) lives in `brand.yaml`.

## Development

```bash
uv run pytest
uv run ruff check .
```

Fonts in `assets/fonts/` are under the SIL Open Font License (licence files alongside).
