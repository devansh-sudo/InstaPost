# Reviewing the verses (one time, about 20–30 minutes)

Every verse printed on a post comes from `data/scriptures/*.yaml`, never from the AI. Until a
person has checked an entry, posts that cite it always wait for your approval (even at L1/L2).

## What to check for each entry
1. **Text**: every letter, matra, halant (्) and danda (। ॥) against a printed edition.
2. **Label**: the chapter.verse number or the source (e.g. श्रीमद्भगवद्गीता 2.47, ऋग्वेद 7.59.12).
3. **Speaker**: who says it (Gita: श्रीकृष्ण to अर्जुन; dohe: संत कबीर).
4. **Meaning**: says no more than the verse does (it is shown with posts and guides the writer).

Sources: Gita — Gita Press Gorakhpur, or gitasupersite.iitk.ac.in. Kabir — wording differs
between editions; a common, well-attested version is fine (e.g. कबीर ग्रंथावली). Shiva mantras —
a printed stotra book; the Mahamrityunjaya mantra is ऋग्वेद 7.59.12.

Worth a closer look (my notes, not a substitute for your check):
- `gita-3.35` meaning leaves out "स्वधर्मे निधनं श्रेयः" (even dying in one's own dharma is
  better); add it if you want the meaning complete.
- `shiv-mahamrityunjaya` meaning adds "के भय" and omits "माऽमृतात्" (but not from immortality).
  A closer meaning: "...वैसे ही हमें मृत्यु के बंधन से मुक्त करें, अमृतत्व से नहीं।"

## On the server
```bash
cd /mnt/HC_Volume_105724104/instapost
bash deploy/docker.sh verses              # list with status
bash deploy/docker.sh verses review       # go through the pending ones
```
For each verse answer `c` (correct), `w` (wrong: it is never cited until fixed), `s` (skip) or
`q` (quit, progress is kept). The worker restarts by itself afterwards.

To fix a wrong entry: `nano data/scriptures/<file>.yaml` (the review shows the file and line),
correct the text/label/meaning, save, then
`bash deploy/docker.sh verses set <id> reviewed`.

Your changes to these files are kept by `bash update.sh`. If an update changes the same file,
it stops and tells you; nothing is overwritten.
