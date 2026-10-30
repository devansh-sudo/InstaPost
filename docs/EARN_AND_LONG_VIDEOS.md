# Earning: affiliate links, the sutra e-book, long YouTube videos

## Affiliate links (dashboard → Earn)

1. Join an affiliate programme (Amazon Associates India is the easiest; Flipkart, book shops
   and puja-item shops have their own) and make links for products that fit your page:
   Gita Press books, malas, diyas, incense.
2. Add each link on **Earn** with a short Hindi name and the themes it fits.
3. Every new post gets the link that fits it best (same theme first, the least recently used
   one next), with a disclosure line:
   - **YouTube** (Shorts and long videos) and **Facebook**: in the description or caption,
     clickable;
   - **Instagram**: captions can't hold links, so the post says "bio के लिंक में". Put your
     public link page (`https://<your dashboard address>/links`) in your Instagram bio. It
     needs `PUBLIC_BASE_URL` in `.env`.
4. Untick "Add links to new posts" to stop, or pause single links.

The disclosure ("एफ़िलिएट लिंक …", plus Amazon's own sentence for Amazon links) is required
by ASCI rules and by the Amazon programme. Keep it.

## Sutra e-book (Earn → Sutra e-book)

Choose a period (and optionally one theme). The book has one approved sutra per page, in the
same book-page style as the reels, plus a cover with a gold diya, an opening page and a
closing page. It's free to make. You get:

- `ebook.pdf`: the book to sell;
- `preview.pdf`: the first 4 pages to share free (e.g. in stories, or on WhatsApp);
- `cover.jpg`: for posts and the shop page.

To sell it, upload the PDF to a payment page (Instamojo, Razorpay Payment Pages, Gumroad;
₹49–99 is typical) and paste that link on Earn. It then appears on your link page and under
every YouTube video.

## Long YouTube videos (dashboard → Long videos)

Long videos build the 4,000 watch hours YouTube needs before it pays.

**Audio videos.** Upload a long audio (jaap, meditation, katha, a bhajan you have the rights
to) and make a video from it:
- a calm moving background (Ganga night, gold frame, dusk … or your own look);
- the mantra or line on screen, with its meaning;
- optional chapters.

The moving picture is a 12-second seamless loop repeated for the whole audio, so even a
one-hour video takes only a minute or two to make. A 1-hour video is about 400 MB.

**Theme collections.** All published posts of one theme over 30/60/90 days as one chaptered
video, like the weekly one. On the 1st of every month the worker makes one per theme that had
6 or more posts.

Every long video waits in the inbox for your OK, then goes to YouTube only.

### Things to know

- **Rights.** Only upload audio you made or have the right to use. Film songs and other
  channels' bhajans get a copyright claim, and the claimant takes the ad money. Files copied
  to `state/long_audio/` by hand need the "I have the rights" button before use.
- **Videos over 15 minutes** need a verified YouTube account (YouTube Studio → Settings →
  Channel → Feature eligibility → verify by phone).
- **Big uploads.** If an upload through the dashboard fails with "413", raise the limit in
  nginx-proxy-manager: proxy host → Advanced → `client_max_body_size 2000m;`. Or copy the file
  straight to `state/long_audio/` on the server with `scp`.
- **YouTube's daily upload quota** allows about 6 uploads a day through the API: Shorts plus
  long videos together. Approve long videos on days with fewer Shorts if you hit it.
