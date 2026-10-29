# Book-photo reels (Backgrounds)

Reels are made like this (`REEL_STYLE=plate`, the default):

1. A photo of an open book with a **blank** page is picked from the Backgrounds library: the
   least recently used one first.
2. Its page corners are known (found automatically, or clicked by you). The photo is cropped
   to 9:16 around the page and gets the house colour grade: warm highlights, soft shadows,
   light vignette and grain, the same for every post.
3. Our page (header, drawing, highlighted hook, body, verse) is printed onto the blank page in
   the photo's perspective: the ink multiplies the photo's own paper, so its light, gutter
   shadow and grain stay, and nothing is printed onto a thumb or finger.
4. The reel motion is unchanged: the hook is there from the first frame, then the body and
   verse print in line by line, and the loop is seamless.

The text is always drawn by our code, so the Hindi is correct and sharp (AI-made pictures
garble Devanagari), and the verse comes from the reviewed database.

If no photo fits a post (no photos yet, or the text would sit under Instagram's buttons), the
reel uses the classic look (drawn book page over a blurred scene photo). `REEL_STYLE=classic`
in `.env` always uses the classic look.

## Where photos come from
- **Your own** (best): dashboard → Backgrounds → Upload. 10–15 photos of a notebook held open,
  blank page, upright, warm home light (sofa, diya, chai, window). Unique to your page.
- **Unsplash**: searched automatically when fewer than 8 photos are in use (at most every
  6 hours, up to 12 downloads a search), or with **Find more on Unsplash**. The photographer
  is credited in the post's `image_credits.txt`.
- Files in `data/plates/` (with `name.json` = `{"quad": [[x,y], [x,y], [x,y], [x,y]]}` for
  top-left, top-right, bottom-right, bottom-left) are used as they are.

## Checking photos
Every photo is checked: a page must be found, blank, and big enough to read once cropped. A
photo the checker is sure about is used straight away. Otherwise it waits under **Waiting for
your check** with its page outline and an example post. **Use it** if the example looks right,
**Corners** to click the four page corners yourself, or **Don't use**.
