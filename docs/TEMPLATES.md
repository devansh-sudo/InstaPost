# Post looks (templates)

A feed where every post looks the same gets scrolled past. Reels, carousels and stories now
rotate between several looks each. All the "free" ones use backgrounds drawn in code, so they
cost nothing, never fail, and shift a little with every post (the lights and the sun move).

| Background | What it is |
|---|---|
| Paper | The cream printed page |
| Mandala | The cream page with a faint hand-drawn mandala behind the text |
| Night | Dark paper, cream text, amber highlight, a few far-away diya lights |
| Dusk sky | A sunset sky (indigo to saffron) with floating lights, cream text |

| Kind | Looks |
|---|---|
| Reels | Book photo (needs approved photos), Drawn book (image API), Full page, Mandala page, Night page, Dusk sky |
| Carousels | Paper, Mandala, Night, Dusk sky |
| Stories | Card on photo (image API), Full page, Mandala, Night, Dusk sky |

Stories always keep the lower part empty, so the poll or question sticker can go there in the
Instagram app (the API cannot add stickers).

## How a look is chosen

1. If you picked one (Create → "Look"), that one.
2. Otherwise the learner's pick among the looks switched on for that kind (dashboard →
   **Templates**): looks that score well come up more often, new ones still get turns, and
   the last few used go to the back.
3. If a reel look can't be made (no approved book photo fits), the next one is used.

The look used is saved in each post's folder (`template.txt`) and in its learning features
(`night` for reels, `carousel/night`, `story/dusk` ...), so dashboard → Learning → "Reel looks"
shows which ones work for your audience.

## Changing the look of a post

- Dashboard → open the post → **Change look**. Pick a look or leave it on "auto".
- Telegram → **🎨 Other look (same text)** (reels, carousels and stories).

The same text is re-used: no new writing is paid for, only the cheap fact check runs again.
The old version is rejected ("new look") and the new one takes its date and slot.

## Book photos from Unsplash

Unsplash search results vary a lot (stacks of closed books, shelves, printed pages), so they
are **never used without your approval**. Check them on the Backgrounds page.
