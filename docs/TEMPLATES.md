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
| Gold frame | Deep maroon with an ornate gold border; marigold petals drift down in reels |
| Sunrise | Warm saffron light with soft rays from the top, dark text |
| Ganga night | Diyas on the far bank of the river, their light on the water; embers rise in reels |
| Manuscript | An old parchment page with darkened, uneven edges |

In reels, the dark looks (Night, Dusk, Ganga) have diya embers rising slowly; Gold frame has
marigold petals falling. Both loop without a seam.

| Kind | Looks |
|---|---|
| Reels | Book photo (needs approved photos), Drawn book (image API), Full page, Mandala page, Night page, Dusk sky |
| Carousels | Paper, Mandala, Night, Dusk sky |
| Stories | Card on photo (image API), Full page, Mandala, Night, Dusk sky |

Stories always keep the lower part empty, so the poll or question sticker can go there in the
Instagram app (the API cannot add stickers).

## Create your own look

Dashboard → Templates → **Create your own look**. Pick one background:

- **Two colours** (free): top and bottom colour, plus an optional pattern (mandala, diya
  lights, light rays);
- **My picture** (free): upload any photo or painting;
- **AI paints it once** (~$0.04): describe it ("Kashi ghats at dusk, diya light on the
  Ganga"); Gemini paints it once and every post after that uses it for free.

Then set the text colour (light or dark), the highlight colour, how much to darken or lighten
the background (so text always reads), the motion in reels, and which kinds of post use it.
A new look is switched on straight away and joins the rotation. Pictures are cropped a little
differently for every post. Delete a look with the button under its example.

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

## Cost per post

Looks drawn in code (all the free ones, and your own looks) cost nothing per post. The cost
of a post is the writing and checking:

- the writer thinks at `WRITER_EFFORT=low` for reels and carousels (set `medium` for longer
  thinking, about twice the cost);
- the fact check runs on the small model first (`FACTCHECK_FIRST_MODEL`, about 15x cheaper)
  and only goes to the careful model when the small one does not pass the post;
- blurred scene photos ("Drawn book", "Card on photo") come from Unsplash or your library
  first (`SCENE_IMAGE_PROVIDER`), and from the image API only when those fail.
