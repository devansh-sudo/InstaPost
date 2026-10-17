# InstaPost — Product & Technical Plan (v0.1, draft for review)

> Status: **planning only, no code yet.** Things marked ⚠️ VERIFY are facts that change often
> (Meta API limits, monetization thresholds, vendor prices). They get checked against the live
> docs on day 1 of the build, before we depend on them.

---

## 1. What I understood from the reference screenshot

The reference is a **"POV book page" reel**. That format works for these reasons:

| Element | What it is | Why it works |
|---|---|---|
| Scene | A hand holding an open book, golden-hour light, diya/lantern, money plant, chai cup, balcony | "Cozy calm" scene. It looks real, so the viewer's brain reads it as a real moment and not as an ad |
| Illustration | A pencil sketch of Shiva meditating in the Himalayas (trishul, moon, mountains) | Instant recognition, devotional, not loud |
| Hook text | Highlighted line: *"जिसे बदल नहीं सकते, उसे स्वीकार करना ही शांति है।"* | Short and relatable. The highlighter draws the eye, so people read it in about 2 seconds |
| Body | 2-line explanation | Rewards the people who keep reading, which gives more watch time |
| Divider + reference | ॐ divider, "संदर्भ: श्रीमद्भगवद्गीता 2.48" | Adds authority, and people **save** it |
| Page number "108" | A sacred number | A small detail that people comment on |

**Problems in the reference itself (we must not repeat them):**
1. **The attribution is wrong.** It says "शिव हमें सिखाते हैं…" but cites **Bhagavad Gita 2.48**, which is Krishna speaking to Arjuna, not Shiva. Devotional audiences notice this and complain in the comments. → We need a **fact-check step** for every scripture reference.
2. **The AI broke the text.** "श्रीमद्गगवद्रीता" is misspelled, and the footer text is gibberish. Image models are bad at Devanagari. → **The image model never writes text.** All text is drawn by our own code with real fonts.
3. **It is someone else's page** (there is an IG watermark at the bottom). Instagram ranks reposted or aggregated content lower and pushes **original** content. → We copy the *format*, never the content.

---

## 2. Honest reality check (please read before we build)

1. **There is no "algorithm crack".** Anyone selling one is selling a story. Instagram has said publicly what it ranks Reels on: how likely people are to **watch to the end, rewatch, send by DM ("sends"), save, and like**, plus **originality**. The real "crack" is to make content that wins on those signals, post consistently, and learn from your own data faster than a human could. That is what this agent does.
2. **Instagram monetization ≠ YouTube.** Instagram does **not** pay a general ad-revenue share per view. Options for an Indian account ⚠️ VERIFY in Professional Dashboard → Monetization:
   - **Gifts** on Reels (follower threshold applies)
   - **Subscriptions** (follower threshold, around 10k historically)
   - **Branded content / sponsorships**. For this niche this is realistically the biggest income: brands selling rudraksha, puja items, books, meditation apps
   - **Facebook content monetization**: cross-posting the same reels to a Facebook Page can earn per-view money there
   - Your own products later (a wallpaper pack, an e-book of "Jeevan Sutra")
3. **Meta's API cannot attach Instagram's music library or trending audio.** Reels published through the API can only carry audio that is **baked into the video file**. Copyrighted Bollywood or bhajan tracks baked in will get muted or taken down. Trending audio can only be added **by hand in the app**. This is the biggest product decision → see Question Q3.
4. **Per-second retention graphs are not available in the API.** We get views, reach, likes, comments, saves, shares, average watch time and total watch time ⚠️ VERIFY exact metric names. The learning loop uses *average watch time ÷ video length* as a stand-in for retention.
5. **Never use unofficial or "private API" libraries** (instagrapi, Selenium bots, auto-follow/auto-DM tools). They are the fastest way to get shadow-limited or banned. We use **only the official Instagram Graph API**.
6. **AI-content labelling.** Meta asks for disclosure of photorealistic AI imagery and may add an "AI info" label automatically. That is fine and does not kill reach. Hiding it is the real risk.
7. **The niche is crowded.** Hindi spiritual and Shiva quote pages are everywhere. We win on **quality + correctness + consistency + a recognisable signature style**, not on volume.

---

## 3. Product definition

**Goal:** grow a Hindi spiritual / peace-of-mind page with original, correct, calm content, in a way that is eligible for monetization, while you can see and control everything.

**Content pillars (first version, the agent re-weights them from data):**

| # | Pillar | Format | Purpose |
|---|---|---|---|
| P1 | **Jeevan Sutra** (the reference format) | Reel, 7–12 s, book-page POV, slow motion | Reach + rewatch |
| P2 | **Shiv Dhyan**: calm Shiva art + one line + ambient sound | Reel, 15–30 s | Watch time, sends |
| P3 | **Shlok + arth** (Gita, Shiv Purana, Upanishads, Kabir) | Carousel, 5–7 slides | Saves (a strong signal) |
| P4 | **Aaj ka Sankalp / mantra** | Stories: poll, quiz, question box, countdown | Keeps followers engaged, so they see posts first |
| P5 | Festival / special days (Mahashivratri, Sawan Somvar, Pradosh, Ekadashi) | All formats | Seasonal spikes, planned 2–3 weeks ahead |

**Starting cadence (the agent changes it after week 2 using data):**
- Reels: **1 per day** in weeks 1–2, then **2 per day** if quality holds
- Carousels: **3 per week**
- Stories: **3–5 per day** (morning mantra, a poll, a reshare of today's reel, a night "shubh ratri" line)
- Posting windows to test (IST): **6:30–8:00** (morning puja time), **12:30–14:00**, **20:00–22:30**
- Monday (Shiv's day) and Pradosh days get extra content
- Hashtags: **3–5 specific ones** (Instagram now limits and discounts hashtag stuffing ⚠️ VERIFY current cap). Keywords inside the caption matter more now (Instagram search).

**"Stop the scroll + watch time" rules every piece must pass (an automatic QA gate):**
1. The first frame already shows the hook line. No logo intro, no black frame.
2. The hook is **12 words or fewer** and can be read in about 2 seconds.
3. Text sits inside the safe area: not under the caption or buttons, not in the top 14% or bottom 20%.
4. The video length is about how long it takes to read everything, +1.5 s. It **loops cleanly** so people rewatch.
5. There is subtle motion (steam, diya flicker, slow push-in, page parallax), so it is never a frozen still.
6. The caption opens with one line that adds meaning (and makes people tap "more"), then a question that invites a comment, then a soft CTA ("जिसे ये सुनना ज़रूरी है, उसे भेजें 🙏"). Sends are the strongest signal.
7. Scripture references pass the fact check, or the post is not published.

---

## 4. How the agent works

```
                 ┌───────────────────── Admin Dashboard (you) ─────────────────────┐
                 │ live jobs · approval queue · calendar · stats · reports · KILL  │
                 └────────────▲──────────────────────────────▲──────────────────────┘
                              │                              │
  ┌───────────┐   ┌───────────┴──┐   ┌──────────────┐   ┌────┴─────────┐   ┌───────────┐
  │ 1.Collector│→ │ 2.Analyst /  │ → │ 3. Planner   │ → │ 4. Studio    │ → │5.Publisher│
  │ IG insights│  │   Learner    │   │ weekly plan +│   │ write→check→ │   │ Graph API │
  │ every 6h   │  │ scores, bandit│   │ daily slots  │   │ image→render │   │ + retries │
  └───────────┘   └──────────────┘   └──────────────┘   │ →QA gate     │   └───────────┘
        ▲                 │                              └──────────────┘         │
        └──────── 6. Reporter (daily digest, weekly deep report) ◄────────────────┘
```

1. **Collector** (plain code, no AI). Every 6 hours it pulls account and per-post insights. Posts get extra pulls at +1h, +3h, +24h, +72h and +7d so we can see the growth curve.
2. **Analyst / Learner**
   - Gives each post a **score** = weighted mix of: shares÷reach, saves÷reach, avg-watch-time÷length, comments÷reach, follows gained (if exposed ⚠️ VERIFY).
   - Tags every post with *features*: pillar, hook style (question / statement / "agar…to…"), deity/scripture, background scene, length, posting slot, caption length, CTA type, audio type.
   - A **Thompson-sampling bandit** picks feature combinations, about 80% "best known" and 20% "experiments". This is the real "self-learning": simple, explainable, it works with small data, and it cannot run away and do something strange.
   - Once a week an LLM writes a **plain-language analysis**: what worked, what didn't, what to try next. You can read it and override it.
   - Note: the signals are noisy until about 30–50 posts exist. The dashboard shows confidence and does not pretend.
3. **Planner.** Every Sunday it builds a 7-day plan (slots, pillar, topic, hook idea, format, audio suggestion, hashtags) from the bandit + the festival calendar + the content backlog. You can edit or approve it in the dashboard.
4. **Studio** (one piece of content)
   1. *Writer* (LLM): hook, body, ॐ divider text, reference, caption, hashtags, alt text, and 3 variants of each
   2. *Fact-checker* (LLM + a local verse database): checks that the verse text, chapter:verse number and speaker are correct, and flags anything uncertain → human review
   3. *Safety / tone check*: nothing sectarian, political, fear-based ("share or bad luck"), medical claims, or mocking of any religion
   4. *Image generator*: the scene and sketch illustration **with no text in it**
   5. *Renderer* (code): composes the page, puts the Devanagari text on with real fonts, adds the highlighter effect, motion, and audio → 1080×1920 MP4 (H.264, AAC) + cover image
   6. *QA gate*: automatic checks from §3 + a vision-model check ("is the text readable, is anything cut off, is the anatomy OK") + a duplicate check against older posts
5. **Publisher.** Graph API container → poll status → publish. It respects the daily publishing limit, retries with backoff, and is **idempotent** (it will never double-post).
6. **Reporter.** A daily digest (Telegram or email) and a weekly report page in the dashboard.

**Autonomy levels (you choose, and you can change it any time from the dashboard):**
- **L0 Manual**: the agent creates and you approve every item *(recommended for weeks 1–3)*
- **L1 Semi-auto**: stories and carousels post automatically, reels wait for approval
- **L2 Auto**: everything posts automatically, and you get a notification with an "undo/delete" button
- **Kill switch**: one button stops all publishing immediately

---

## 5. Tech stack

| Layer | Choice | Free / open source? | Why |
|---|---|---|---|
| Language | **Python 3.12** | ✅ | Best libraries for media, AI and data |
| API + workers | **FastAPI** + **APScheduler** (later Celery/Redis if needed) | ✅ | Simple, async, one service |
| Database | **PostgreSQL 16** (self-hosted, or Supabase free tier) | ✅ | Posts, insights time series, plans, audit log |
| Media storage | **Cloudflare R2** (10 GB free, no egress fees) | ✅ free tier | The Graph API needs a **public URL** for each video. We use short-lived signed URLs |
| Video rendering | **FFmpeg** + **MoviePy** | ✅ | Ken-Burns, parallax, loops, audio mix |
| Text rendering | **Pillow with libraqm (HarfBuzz)** for correct Devanagari conjuncts | ✅ | Plain Pillow breaks Hindi matras. This is a common bug |
| Fonts | Noto Serif Devanagari, Tiro Devanagari Hindi, Mukta, Yatra One (all OFL) | ✅ | Free for commercial use |
| LLM | **Claude API**: Opus 5.5 for weekly strategy + fact check, Sonnet 5.5 for writing, Haiku 5.5 for tagging/classification | 💲 paid | Strong Hindi, good reasoning, tool use |
| Image generation | Option A **paid API** (Gemini image / gpt-image / Ideogram) ⚠️ VERIFY prices · Option B **self-hosted FLUX.1-schnell** (Apache-2.0) or **SDXL** (OpenRAIL++) on a rented GPU | A 💲 / B ✅ | ⚠️ **FLUX.1-dev is non-commercial, so we can't use it** for a monetized page |
| Optional motion | Wan 2.x image-to-video (Apache-2.0) on GPU, *later* | ✅ | Only if experiments show real motion beats FFmpeg motion |
| Optional voice | AI4Bharat Indic Parler-TTS (open) or ElevenLabs (💲) | ✅ / 💲 | For "Shiv Dhyan" narration tests |
| Audio | Self-made ambience (tanpura drone, bells, wind, made with code) + properly licensed tracks only | ✅ | Avoids copyright mutes |
| Dashboard | **Next.js + Tailwind + shadcn/ui + Recharts**, or a lighter FastAPI + HTMX version | ✅ | See §7 |
| Auth & access | **Cloudflare Access** (Zero Trust, free up to 50 users) in front of the dashboard + app login with TOTP 2FA | ✅ | The dashboard is never openly on the internet |
| Secrets | `.env` encrypted with **SOPS + age**, or Doppler free tier | ✅ | No secrets in git, ever |
| Notifications | **Telegram bot** (approve / reject buttons from your phone) | ✅ | Fastest approval loop |
| Monitoring | Uptime Kuma + structured logs; Sentry free tier optional | ✅ | |
| Deploy | Docker Compose on one VPS | ✅ | |

---

## 6. Compute size & monthly cost (estimate, ⚠️ VERIFY vendor prices at build time)

**No GPU needed** if we use a paid image API. Rendering a 10-second 1080×1920 reel with FFmpeg takes seconds on a CPU.

| Item | Spec | Approx. cost / month |
|---|---|---|
| VPS | 2 vCPU · 4 GB RAM · 40–80 GB SSD (e.g. Hetzner CX22-class) | ~€4–6 (~₹400–600) |
| (Free alternative) | Oracle Cloud Always-Free ARM (4 OCPU / 24 GB) | ₹0, but sign-up and capacity can be unreliable |
| Claude API | ~2 reels + 1 carousel + stories per day, weekly deep analysis, prompt caching on | **~$10–35** (Opus 5.5 $4/$20 per M tokens, Sonnet 5.5 $2/$10, Haiku 5.5 $0.10/$0.50) |
| Image API | ~100–150 images/month incl. rejected ones × ~$0.02–0.05 | **~$3–8** |
| Cloudflare R2 + Access + Tunnel | Free tiers | $0 |
| Domain (optional, for the dashboard) | .in / .com | ~₹800–1,200 per year |
| **Total** | | **≈ $20–50 / month (~₹1,700–4,200)** |

If you want **self-hosted image models** instead: a serverless GPU (RunPod / Modal / Replicate-style, 24 GB VRAM) costs roughly $0.0003–0.001 per second of compute. That is about the same as the paid API at this volume. A GPU running all month (~$150–400) is **not worth it**.

---

## 7. Admin dashboard (what you will see)

1. **Live ops**: the job currently running and its step ("Rendering reel #142, step 5/6"), the queue, the last 50 log lines, error alerts, the **KILL SWITCH**, and the autonomy level selector
2. **Approval queue**: a preview of each reel/carousel/story, the caption, the fact-check result with sources, and *Approve / Edit / Regenerate / Reject* buttons
3. **Calendar**: the week's plan, drag to reschedule, festival markers
4. **Stats**: followers (daily change), reach, views, avg watch time, sends, saves, profile visits; per-post table with score; best posting slots heatmap; pillar comparison; growth curves
5. **Learning**: what the bandit currently believes (with confidence), active experiments, the weekly AI analysis
6. **Monetization readiness**: follower count against the thresholds ⚠️ VERIFY, how much of your audience is in India vs. elsewhere, brand-friendliness checklist
7. **Audit log**: every action the agent took, with time, who triggered it (agent / you), and the API response
8. **Settings**: token status and expiry, posting windows, pillar weights, banned words/topics, brand style

---

## 8. Security design

1. **Official Graph API only.** We use a long-lived token with the smallest set of permissions (publish content, read insights, manage comments only if needed). **Never your Instagram password.**
2. Tokens are encrypted at rest and stored only on the server. They are refreshed automatically before they expire (~60 days), and you get an alert if a refresh fails.
3. The dashboard sits behind Cloudflare Access + 2FA, is reachable only through a Cloudflare Tunnel, and the VPS has **no open ports** except SSH (key-only, fail2ban).
4. **Prompt-injection guard.** Comments, DMs and web pages count as *untrusted data*. The agent never follows instructions found in them, and the LLM has **no tool that can publish directly**. Publishing is plain code that runs only after the QA gate + your approval/autonomy rule.
5. Rate limits and budget caps: max posts per day, max LLM/image spend per day. If a cap is hit, the system pauses and alerts you.
6. No auto-follow, auto-like, auto-DM or comment spam. Those break Meta's policies.
7. The audit log can only be appended to. Daily encrypted DB backups go to R2.
8. Dependency pinning + Dependabot. Secrets are scanned in CI.

**How you share credentials (later, not now):** never paste them into chat. You put them into the server's encrypted secrets file yourself, or into the dashboard's Settings page over HTTPS. I will give exact steps.

---

## 9. Build phases & timeline

| Phase | What | Duration |
|---|---|---|
| 0 | Accounts: IG Professional (Creator) account, Meta developer app, R2, VPS, Claude API key; check the real API limits | 2–4 days (Meta app review can add time) |
| 1 | Studio MVP: writer + fact-check + image + Devanagari renderer + QA → files you can post by hand | ~1 week |
| 2 | Publisher + approval via Telegram + basic dashboard (queue, logs, kill switch) | ~1 week |
| 3 | Collector + stats dashboard + daily report | ~1 week |
| 4 | Learner (scoring + bandit) + weekly planner + weekly AI report | ~1–2 weeks |
| 5 | Raise autonomy, festival calendar, Facebook cross-post, A/B of formats | ongoing |

The first real post can go out at the end of Phase 1, posted by hand, so growth starts while we build.

---

## 10. Growth expectations (honest ranges, not promises)

These assume: 1–2 good reels per day, never skipping a day, original content, and the learning loop running. A new account starting from about 0.

| Time | Conservative | Base case | Optimistic (1–3 reels go viral) |
|---|---|---|---|
| Month 1 | 100–500 | 500–2k | 5k+ |
| Month 3 | 1k–3k | 5k–15k | 30k–80k |
| Month 6 | 3k–8k | 15k–40k | 100k+ |
| Month 12 | 8k–20k | 40k–120k | 300k+ |

- Growth in this niche is **step-shaped**: flat for weeks, then one reel takes off. Consistency is what puts you in a position to get those jumps.
- The biggest single factor is **content quality and a recognisable style**, more than automation.
- Weekly checkpoints: if the average score has not improved after about 6 weeks, we change the format, not just the posting times.

---

## 11. Open questions for you (answered → §12)

See the chat message. Answers go into §12 here once you decide.

## 12. Decisions log

| # | Topic | Decision |
|---|---|---|
| D1 | Accounts | New Instagram, Facebook and YouTube accounts, all starting from 0. → Publish the same piece to **IG Reels + FB Reels + YouTube Shorts** (§13) |
| D2 | Language | **Hindi + Hinglish**: Hindi (Devanagari) on visuals; caption = Hindi + one short Hinglish line. The A/B data can shift the mix later |
| D3 | Audio | **Let the data decide.** Arms: (a) calm ambience only, (b) ambience + soft Hindi voice-over reading the sutra, (c) you add trending audio by hand from your phone. Only voice-over makes speech, nothing is "sung" |
| D4 | Autonomy | Start at **L0: you approve everything**. Plus **batch approval**: "approve next post", "approve next N", "approve all of tomorrow" from the dashboard or Telegram |
| D5 | Images | **Paid image API**, the cheapest one that passes the quality bar (decided by a short bake-off in Phase 1) |
| D6 | Hosting | **Your own VPS** → ₹0 hosting cost. Specs/OS still needed |
| D7 | Dashboard | Build it (Next.js + shadcn/ui, behind Cloudflare Access) |
| D8 | Budget | **$15–20/month hard cap** (§14) |
| D9 | Brand | **Customisable template system**: page name, handle, fonts, colours, paper texture, divider symbol, scene presets are all set in one `brand.yaml` and editable from the dashboard |
| D10 | Scope | Shiva, Krishna/Gita, Kabir, general life lessons. Mix and topics chosen by the planner from data + calendar |
| D11 | "Algorithm crack" | = data-driven content planning + post creation (no posts *about* growth) |
| D12 | Notifications | **Telegram bot** for approvals and daily reports (Phase 2) |
| D14 | Dashboard stack | **Changed from Next.js to server-rendered FastAPI + htmx**: one service on the VPS, no Node build, smaller attack surface. Revisit for Phase 3 charts if needed |
| D16 | Learning scope | The planner decides *what* (pillar, hook, scene, slot, length, topic); *how much* stays with you (schedule.yaml). The weekly AI review only advises |
| D17 | Festivals | Dates in data/calendar/festivals.yaml are used only after you confirm them |
| D15 | Database | SQLite (WAL) by default; PostgreSQL via `DATABASE_URL` |
| D13 | Stories | The API can publish story images/videos but **cannot add poll/quiz stickers**. The studio makes the story image + a `story_poll.txt`; you add the sticker in the app (about 20 s) |

---

## 13. Multi-platform publishing (new, from D1)

One render → three platforms. This roughly triples the reach of each piece for almost no extra cost.

| Platform | How | Why it matters | ⚠️ VERIFY |
|---|---|---|---|
| Instagram | Graph API (Instagram Login, Professional account) | Main brand, sends/saves | Daily publish limit |
| Facebook Reels | Pages API (video_reels) | FB Reels can pay per view if the Page becomes eligible for Content Monetization | Eligibility for new Pages |
| YouTube Shorts | YouTube Data API v3 `videos.insert` | **The only platform with real per-view ad revenue share** (YouTube Partner Program: 1k subscribers + 10M Shorts views in 90 days for ad share; lower tier for fan funding) | Upload quota (~6 uploads/day on the default 10k units). **Uploads from unverified API projects are forced to private until Google audits the app**, so we apply for the audit in Phase 0 |

Rules: the caption and title are adapted per platform. Each platform gets its own learning signal, because what works on IG may not work on Shorts. New accounts warm up: **no more than 1 post per platform per day in week 1**, and you also use the accounts normally from your phone.

---

## 14. Fitting the $15–20/month budget

Your VPS = $0. The budget below is then API spend only. It is enforced by code: **$0.60/day soft cap, $18/month hard cap**. When the cap is hit, publishing of already-approved items continues and new generation pauses.

| Item | How we keep it cheap | Est. / month |
|---|---|---|
| Writing + fact-check | Sonnet 5.5, prompt caching. Tomorrow's content is generated tonight with the **Batch API (50% off)** | ~$4–6 |
| Tagging, QA, story text | Haiku 5.5 ($0.10/$0.50 per M tokens) | ~$0.5–1 |
| Weekly strategy report | Opus 5.5, once a week | ~$1.5–2.5 |
| Images | ~90–120 images × ~$0.03–0.04. Stories and carousels reuse the reel's image | ~$3–5 |
| Voice-over (arm b) | Open-source TTS on the VPS (Indic Parler-TTS, CPU, slow but free) | $0 |
| R2 / Cloudflare / Telegram | Free tiers | $0 |
| **Total** | | **≈ $10–15**, leaving a small buffer |

Volume this budget supports: 1–2 reels/day (each sent to 3 platforms), 3 carousels/week, 3–5 stories/day.

---

## 15. Earnings projection (base case, very uncertain)

Spend ≈ $15–20/month (₹1,300–1,700). Earnings come late and come mostly from **brand deals**, not views.

| Period | Instagram | YouTube Shorts | Facebook | Brand deals | Total / month (base) |
|---|---|---|---|---|---|
| Month 1–3 | ₹0 (not eligible yet) | ₹0 (YPP not reached) | ₹0 | ₹0 | **₹0** → you are investing ~₹4–5k in total |
| Month 4–6 | Gifts, small: ₹0–2k | Maybe YPP if one Short goes viral. Indian Shorts RPM is low (~₹2–8 per 1,000 views) | ₹0–2k if eligible | 0–2 small deals × ₹1–5k | **₹0–10k** |
| Month 7–12 | Gifts/Subscriptions ₹1–5k | ₹1–8k | ₹1–5k | 2–4 deals × ₹3–15k | **₹5k–40k** |
| Optimistic (viral) month 12 | | | | 100k+ followers: ₹15–50k per deal | ₹50k–1.5L+ |

The **break-even** on the API spend (~₹1,700/month) is realistic around **month 4–8** in the base case. These numbers are estimates from typical Indian creator rates, not guarantees. A page can also stay below break-even if the content doesn't connect, and the 6-week checkpoint in §10 exists to catch that early.

---

## 16. Build status

| Phase | Status |
|---|---|
| 1. Studio MVP (writer, safety, fact-check, images, Hindi renderer, reel/carousel/story, QA gate, budget caps) | ✅ Built and tested offline. Needs real API keys for the first live run |
| 2. Publisher + Telegram approval + dashboard | ✅ Built and tested with mocked platform APIs and a demo run. Needs real accounts/tokens for the first live post |
| 3. Collector + stats | ✅ Built and tested with mocked platform APIs and seeded demo data. Metric names are verified against the live APIs at the first real run |
| 4. Learner + planner + weekly report | ✅ Built and tested (simulated learning, planner rules, fake-API review run). Real conclusions need ~30–50 real posts |
| 5. Batch API generation (−50% writing cost) | Deferred: ~$2–3/month saving vs. a larger rework; do it if spend nears the cap |

## 17. What I need from you

**Now (for the first live Phase 1 run):**
1. **Anthropic API key**: console.anthropic.com → API keys. Set a monthly spend limit there as a second safety net.
2. **Image API key**: Google AI Studio (Gemini) key, and optionally an OpenAI key so we can compare quality on 5 test images and keep the better one.
3. **Your Instagram handle and page name**, for `brand.yaml`.
4. **Review the verse database** (`uv run instapost verses`): compare each verse with a printed edition (Gita Press for the Gita) and change `review_status` to `reviewed`. Until then, every post citing a verse is marked for human fact review.
5. **VPS details**: CPU/RAM, OS version, whether Docker is installed, and SSH access for deployment (or you run the setup commands in the README yourself).

Keys go into `.env` on the VPS (`chmod 600`), **never into chat or git**.

**Before Phase 2 (publishing):**
6. Instagram switched to a **Professional (Creator) account**.
7. A **Facebook Page** for the brand, and a **Meta developer app** with Instagram API access. Step-by-step: [`SETUP_META_YOUTUBE.md`](SETUP_META_YOUTUBE.md)
8. **Google Cloud project** with YouTube Data API enabled, plus the OAuth consent screen. We apply for the API audit so uploads are not forced to private.
9. A **Telegram bot** from @BotFather (just the token) and your Telegram user ID.
10. A **domain** (optional but recommended) for the dashboard behind Cloudflare Access, plus a free Cloudflare account.
