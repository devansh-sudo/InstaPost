# Background music for reels (royalty-free only)

The agent picks a track that fits each reel's pillar from this folder, least recently used
first, fades it in and out, levels its loudness and keeps the soft tanpura ambience
underneath. With an empty folder, reels use the ambience alone.

## Name files by what they are
Words in the name decide which pillar a track is used for:

| Pillar | Words that match |
|---|---|
| Shiva | shiva, om, mahadev, damru, mantra, rudra, chant, temple |
| Gita / Krishna | krishna, flute, bansuri, gita, govinda, radha |
| Kabir | kabir, sitar, ektara, folk, bhajan, doha, santoor |
| Jeevan sutra / life lessons | calm, peace, piano, meditation, flute, morning, soft, hope |

Examples: `krishna-bansuri-calm.mp3`, `shiv-om-chant@12.mp3` (`@12` = start 12 seconds in,
for tracks with a slow intro), `kabir-ektara-folk.mp3`. Aim for 5+ tracks per pillar.

## Where to get tracks you may use
- **Pixabay Music** (pixabay.com/music): free for commercial and monetized social videos, no
  attribution required. Search: bansuri, indian flute, om chant, shiva, sitar, bhajan,
  meditation, tanpura.
- **YouTube Audio Library** (YouTube Studio → Audio library): free; filter "Attribution not
  required".
- Music you made yourself, or bought with a licence that covers social media.

**Never** put film or label songs here (T-Series, Bhakti albums, Bollywood bhajans, trending
reel songs downloaded from anywhere). Instagram and Facebook mute or remove such reels, and
YouTube Content ID sends the earnings to the label, which blocks your own monetization.

## Trending songs
Instagram's trending devotional sounds are licensed only inside the Instagram app, and its
API cannot add them. Use **📲 Trending song** on a reel in Telegram: Facebook and YouTube are
published with your library track, and you get the reel (without music) plus the caption to
post on Instagram with the trending sound. Then send `/posted <id> <link>`.

Files in this folder stay on your machine: git ignores everything except this README.

### Reels on Instagram always by hand (`INSTAGRAM_REELS=app`)
Put `INSTAGRAM_REELS=app` in `.env` and every approved reel works like that: at its time slot
Facebook and YouTube are published automatically and Telegram sends you the Instagram package
(video without music, caption, steps and **which sounds to search**). The reel waits for your
`/posted <id> <link>`. Stories and carousels still post automatically.

### Keep a list of what is trending (`trending.yaml`)
Copy `trending.example.yaml` to `trending.yaml` here and update it once a week:
1. Instagram app → your profile → **Professional dashboard → Trending audio** (or open Reels,
   tap a sound and look for the **↗** arrow: that sound is trending now).
2. Search "shiv", "mahadev", "krishna", "radhe", "bhajan", "flute" in ♫ Audio and note the
   sounds with the ↗ arrow or many reels (tap the sound to see the count).
3. Write their name, search words, mood and pillars into `trending.yaml`.

The bot then suggests the best match for each reel (its pillar and the mood the month plan
gave it). Without the file it suggests search words per pillar. Use a trending sound in the
first 24–72 h of its rise; older ones help less.
