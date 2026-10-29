# Growth and monetization plan (Hindi devotional niche)

Thresholds below are what the platforms published as of late 2026. They change; check the
platform's own monetization page before planning around a number.

## Where the money can come from

| Platform | How it pays | What it needs |
|---|---|---|
| **YouTube** | Ad revenue share (YouTube Partner Program) | 1,000 subscribers **and** either 4,000 public watch hours of long videos in 12 months **or** 10 million Shorts views in 90 days. A lower tier (memberships, Super Thanks) opens at 500 subscribers, 3 uploads in 90 days and 3,000 watch hours or 3 million Shorts views. |
| **Facebook** | Content Monetization (reels, videos, photos) | Invitation-based; check Professional dashboard → Monetization. Original content and steady posting matter most. |
| **Instagram** | No ad revenue share in India. Gifts on reels, Subscriptions, brand deals, affiliate links | Gifts and Subscriptions need a professional account in good standing (Subscriptions: 10,000 followers, 18+). Brand deals start around 10k engaged followers. |

**What this means for the plan:** Shorts views count for YouTube, but 10 million in 90 days is a
lot for a new channel. Long videos reach 4,000 hours faster: one 30-minute video watched 10
minutes by 600 people is 100 hours. A devotional channel needs some long videos.

## What to add (in order of value)

1. **Weekly long video for YouTube (biggest gap).** Every Sunday, one 8–15 minute video, e.g.:
   - "इस हफ़्ते के 7 जीवन सूत्र": the week's reels joined with a short intro and outro.
   - "गीता अध्याय 2 सरल भाषा में": the verses from the database with meaning, slow visuals, calm voice.
   - Monthly: a 30–60 minute meditation or chant (royalty-free om/flute music + slow Shiva visuals).
     These earn watch hours fastest. YouTube rejects channels full of near-identical loops
     ("reused/repetitive content"), so each one needs its own visuals, title and description.
   **Built:** the weekly compilation is made automatically (see "Weekly YouTube video" below).
2. **Carousels to Facebook too.** Today carousels go to Instagram only (`schedule.yaml` →
   `targets`). Facebook photo posts reach a different, older audience that shares devotional
   content a lot.
3. **Instagram Stories with a poll every day** (already in the plan). Polls raise reach for the
   next reel because Instagram sees people interacting with you.
4. **Trending sound on every Instagram reel** (`INSTAGRAM_REELS=app`). Strongest reach lever on
   Instagram for this niche.
5. **Festival and Monday (Somvar) content a few days early.** Search for "Diwali status" or
   "Mahashivratri" peaks 2–5 days before the day.
6. **Comment replies within the first hour.** Reach depends on early interaction. A quick reply
   (even 🙏) to each comment helps; the dashboard can collect comments later if you want.
7. **A free product for the bio link** (later, at ~5k followers): a "21 दिन गीता सूत्र" PDF or phone
   wallpapers of the best posts. It builds an email/WhatsApp list you own, which brand deals and
   your own products can use later.

## Income ideas that fit this niche (and the page's rules)

- **Affiliate:** Bhagavad Gita books (Gita Press, simple Hindi translations), japa malas, diyas,
  incense, meditation cushions. Amazon India Associates works with a bio-link page.
- **Brand deals:** devotional apps, puja-samagri stores, Ayurvedic and wellness brands,
  spiritual book publishers. Stay away from fear-based astrology or "remedy" offers: they go
  against the page's no-fear rule and lose trust.
- **Your own products later:** wallpapers, printable verse cards, a short paid course
  ("Gita for everyday stress").

## Monthly targets (realistic for a new page)

| Month | Instagram | YouTube | Facebook |
|---|---|---|---|
| 1 | 300–1,000 followers; find the 2 best pillars | 50–200 subscribers; start the weekly long video | 100–500 followers |
| 3 | 3k–10k | 500–1,000 (lower tier) | 2k–5k |
| 6 | 10k–30k: brand deals, Subscriptions | 1,000 + 4,000 hours: full YPP | Content Monetization invite likely |

These depend on post quality and consistency more than on anything else. The Analytics and
Learning pages show which pillar, hook style and time slot work, and the weekly plan follows them.

## Weekly YouTube video (built)

Every Sunday at 16:30 (`schedule.yaml` → `weekly_video`) the worker turns the week's
**published** reels, carousels and stories into one landscape video (1920x1080):

- intro card "इस हफ़्ते के N जीवन सूत्र" with the dates, then one chapter per reel and carousel
  ("सूत्र 1", "सूत्र 2"...), the stories as one "छोटी बातें, बड़ी सीख" chapter, and an outro asking to
  subscribe;
- each vertical post sits in the middle over a blurred copy of itself, its number, theme and
  verse reference on the left, the page name and handle on the right; reels loop to about 30 s,
  carousel slides show 8 s each, stories 7 s;
- one continuous, levelled track from your music library under the whole video (the tanpura
  ambience if the library is empty);
- title, description with YouTube chapters (00:00 सूत्र 1 ...), hashtags, tags, and a 1280x720
  thumbnail. Custom thumbnails need a phone-verified channel (youtube.com/verify).

It costs nothing to make (no AI calls) and takes a few minutes of server time. It arrives in
Telegram and the Inbox like any post: approve it and it goes to YouTube at 19:15 (or 10
minutes after you approve, if that time has passed). It needs at least 3 published reels or
carousels in the week. Make one any time with `/weekly` in Telegram, **Create → Make it now**
on the dashboard, or `bash deploy/docker.sh weekly-video`.

With the current plan (8 reels, 3 carousels and 14 stories a week) the video is about 7–9
minutes. Ads in the middle of a video need 8 minutes or more; to be sure of that, raise
`REEL_SECONDS` in `src/instapost/weekly.py` (each reel loops longer).
