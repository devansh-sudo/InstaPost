# Reel looks (templates)

A feed where every reel looks the same gets scrolled past. Reels now rotate between four looks:

| Look | What it is | Cost |
|---|---|---|
| Book photo | The page printed onto a real photo of an open book (dashboard → Backgrounds) | free (needs approved photos) |
| Drawn book | Our drawn book page over a softly blurred scene photo | image API |
| Full page | The whole screen is a cream printed page, close up | free |
| Night page | Dark paper, cream text, amber highlight | free |

## How a look is chosen

1. If you picked one (Create → "Reel look"), that one.
2. Otherwise the learner's pick among the looks switched on (dashboard → **Templates**):
   looks whose reels score well come up more often, new ones still get turns, and the last
   few used go to the back, so the same look never runs many times in a row.
3. If a look can't be made (no approved book photo fits), the next one is used. "Drawn book"
   is always the last fallback.

The look used is saved in each post's folder (`template.txt`) and in its learning features, so
dashboard → Learning → "Reel looks" shows which ones work for your audience.

## Changing the look of a reel

- Dashboard → open the reel → **Change look**. Pick a look or leave it on "auto".
- Telegram → **🎨 Other look (same text)**.

The same text is re-used: no new writing is paid for, only the cheap fact check runs again.
The old version is rejected ("new look") and the new one takes its date and slot.

## Book photos from Unsplash

Unsplash search results vary a lot (stacks of closed books, shelves, printed pages), so they
are **never used without your approval**. Check them on the Backgrounds page: look at the
example post, then click **Use** or set the page corners yourself. Your own uploads with a
clean, single open page are still used straight away.
